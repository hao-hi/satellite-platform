"""Generic lightweight study runner for platform-style experiments."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from typing import Any

from satmodel._version import __version__
from satmodel.config import ScenarioSpec, compile_scenario, load_scenario, scenario_from_mapping, scenario_to_mapping
from satmodel.io import ResultWriter, WrittenResult
from satmodel.system import ScenarioRunner


def set_mapping_path(mapping: dict[str, Any], path: str, value: Any):
    """Set a dotted path inside a nested scenario mapping."""

    parts = path.split(".")
    cursor = mapping
    for part in parts[:-1]:
        if not isinstance(cursor, dict) or part not in cursor:
            raise ValueError(f"unknown scenario path: {path}")
        cursor = cursor[part]
    leaf = parts[-1]
    if not isinstance(cursor, dict) or leaf not in cursor:
        raise ValueError(f"unknown scenario path: {path}")
    cursor[leaf] = value


@dataclass(frozen=True)
class Sweep:
    """A simple Cartesian parameter sweep."""

    path: str
    values: tuple[Any, ...]

    def __init__(self, path: str, values):
        object.__setattr__(self, "path", str(path))
        object.__setattr__(self, "values", tuple(values))
        if not self.path:
            raise ValueError("sweep path must be non-empty")
        if not self.values:
            raise ValueError("sweep values must be non-empty")


@dataclass(frozen=True)
class MonteCarlo:
    """A reproducible Monte Carlo seed series."""

    samples: int
    seed: int = 0
    path: str = "time.seed"

    def __init__(self, samples: int, seed: int = 0, path: str = "time.seed"):
        object.__setattr__(self, "samples", int(samples))
        object.__setattr__(self, "seed", int(seed))
        object.__setattr__(self, "path", str(path))
        if self.samples <= 0:
            raise ValueError("Monte Carlo samples must be positive")
        if not self.path:
            raise ValueError("Monte Carlo seed path must be non-empty")


@dataclass
class StudySummary:
    """Summary of a generic study run."""

    output_dir: Path
    runs: list[WrittenResult] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)

    def metrics_table(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.rows]

    def acceptance_summary(self) -> dict[str, int | float]:
        run_count = len(self.rows)
        accepted_count = sum(1 for row in self.rows if bool(row.get("accepted", True)))
        failed_count = run_count - accepted_count
        acceptance_rate = accepted_count / run_count if run_count else 0.0
        return {
            "run_count": run_count,
            "accepted_count": accepted_count,
            "failed_count": failed_count,
            "acceptance_rate": acceptance_rate,
        }

    def best_row(self, metric: str = "final_error_deg") -> dict[str, Any] | None:
        if not self.rows:
            return None
        candidates = [row for row in self.rows if metric in row]
        if not candidates:
            return dict(self.rows[0])
        return dict(min(candidates, key=lambda row: self._numeric_value(row.get(metric), default=float("inf"))))

    def worst_row(self, metric: str = "final_error_deg") -> dict[str, Any] | None:
        if not self.rows:
            return None
        candidates = [row for row in self.rows if metric in row]
        if not candidates:
            return dict(self.rows[-1])
        return dict(max(candidates, key=lambda row: self._numeric_value(row.get(metric), default=float("-inf"))))

    def parameter_columns(self) -> list[str]:
        return [name for name in self._fieldnames() if name.startswith("param_")]

    def metric_columns(self) -> list[str]:
        metadata = {
            "run_id",
            "scenario",
            "seed",
            "system_builder",
            "controller",
            "environment",
            "fault_count",
            "accepted",
            "failed_acceptance",
            "output_dir",
        }
        columns = []
        for name in self._fieldnames():
            if name in metadata or name.startswith("param_"):
                continue
            if any(self._is_number(row.get(name)) for row in self.rows):
                columns.append(name)
        return columns

    def write_metrics_csv(self, filename: str = "summary_metrics.csv") -> Path:
        path = self.output_dir / filename
        fieldnames = self._fieldnames()
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in self.rows:
                writer.writerow({key: self._cell(row.get(key, "")) for key in fieldnames})
        return path

    def write_manifest(self, filename: str = "study_manifest.json") -> Path:
        path = self.output_dir / filename
        payload = {
            "manifest_version": 1,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "satmodel_version": __version__,
            "run_count": len(self.rows),
            "runs": self.rows,
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        return path

    def write_index(self, filename: str = "index.json") -> Path:
        path = self.output_dir / filename
        acceptance = self.acceptance_summary()
        best = self.best_row()
        payload = {
            "index_version": 1,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "satmodel_version": __version__,
            **acceptance,
            "best_run_id": None if best is None else best.get("run_id"),
            "best_output_dir": None if best is None else best.get("output_dir"),
            "metric_columns": self.metric_columns(),
            "parameter_columns": self.parameter_columns(),
            "runs": self.rows,
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        return path

    def write_markdown(self, filename: str = "README.md") -> Path:
        path = self.output_dir / filename
        acceptance = self.acceptance_summary()
        best = self.best_row()
        worst = self.worst_row()
        parameter_columns = self.parameter_columns()
        metric_columns = self.metric_columns()
        lines = [
            "# satmodel Study Summary",
            "",
            f"- Runs: `{len(self.rows)}`",
            f"- Accepted: `{acceptance['accepted_count']}`",
            f"- Failed: `{acceptance['failed_count']}`",
            f"- Acceptance rate: `{acceptance['acceptance_rate']:.1%}`",
            f"- Best run: `{self._row_value(best, 'run_id')}`",
            f"- Best final error deg: `{self._format_number(self._row_value(best, 'final_error_deg'))}`",
            f"- Worst run: `{self._row_value(worst, 'run_id')}`",
            f"- Worst final error deg: `{self._format_number(self._row_value(worst, 'final_error_deg'))}`",
            "",
            "## Parameters",
            "",
        ]
        if parameter_columns:
            lines.extend(f"- `{column}`" for column in parameter_columns)
        else:
            lines.append("- None")
        lines.extend(["", "## Metrics", ""])
        if metric_columns:
            lines.extend(f"- `{column}`" for column in metric_columns)
        else:
            lines.append("- None")
        lines.extend(
            [
                "",
                "## Runs",
                "",
                "| Run | Scenario | Accepted | Final error deg | RMS error deg | Peak torque N m | Output |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for row in self.rows:
            lines.append(
                "| {run_id} | {scenario} | {accepted} | {final} | {rms} | {peak} | {output} |".format(
                    run_id=row["run_id"],
                    scenario=row["scenario"],
                    accepted=row["accepted"],
                    final=self._format_number(row.get("final_error_deg")),
                    rms=self._format_number(row.get("rms_error_deg")),
                    peak=self._format_number(row.get("peak_torque_nm")),
                    output=row.get("output_dir", ""),
                )
            )
        lines.extend(
            [
                "",
                "## Files",
                "",
                "- `summary_metrics.csv`: one row per run with parameters and metrics.",
                "- `study_manifest.json`: complete study manifest with raw rows.",
                "- `index.json`: compact machine-readable study index.",
            ]
        )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def write_outputs(self) -> dict[str, Path]:
        paths = {
            "summary_metrics": self.write_metrics_csv(),
            "study_manifest": self.write_manifest(),
            "index": self.write_index(),
            "report": self.write_markdown(),
        }
        return paths

    def _fieldnames(self) -> list[str]:
        names: list[str] = []
        for row in self.rows:
            for key in row:
                if key not in names:
                    names.append(key)
        return names

    @staticmethod
    def _cell(value) -> str | int | float:
        if isinstance(value, (str, int, float)):
            return value
        return json.dumps(value, ensure_ascii=False, default=str)

    @staticmethod
    def _is_number(value) -> bool:
        if isinstance(value, bool):
            return False
        try:
            float(value)
        except (TypeError, ValueError):
            return False
        return True

    @staticmethod
    def _numeric_value(value, *, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _format_number(value) -> str:
        try:
            return f"{float(value):.6g}"
        except (TypeError, ValueError):
            return ""

    @staticmethod
    def _row_value(row: dict[str, Any] | None, key: str):
        if row is None:
            return ""
        return row.get(key, "")


class StudyRunner:
    """Run one scenario, a lightweight Cartesian sweep, or Monte Carlo samples."""

    def __init__(self, scenario: ScenarioSpec | dict[str, Any] | str | Path, output_dir: str | Path | None = None):
        if isinstance(scenario, ScenarioSpec):
            self.scenario = scenario
        elif isinstance(scenario, dict):
            self.scenario = scenario_from_mapping(scenario)
        else:
            self.scenario = load_scenario(scenario)
        self.output_dir = Path(output_dir) if output_dir is not None else Path(self.scenario.outputs.root)

    def run(self, *factors: Sweep | MonteCarlo) -> StudySummary:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        cases = self._cases(factors)
        summary = StudySummary(output_dir=self.output_dir)
        for index, (spec, parameters) in enumerate(cases):
            run_id = f"run_{index:03d}"
            run_dir = self.output_dir if len(cases) == 1 else self.output_dir / run_id
            compiled = compile_scenario(spec)
            result = ScenarioRunner(compiled.system).run(compiled.config)
            written = ResultWriter(run_dir).write(result, spec, run_id=run_id, parameters=parameters)
            row = {
                "run_id": run_id,
                "scenario": spec.metadata.name,
                "seed": spec.time.seed,
                "system_builder": spec.system.builder,
                "controller": spec.system.controller,
                "environment": spec.system.environment,
                "fault_count": len(spec.faults),
                "accepted": written.accepted,
                "failed_acceptance": ";".join(written.failed_acceptance),
                **{f"param_{key}": value for key, value in parameters.items()},
                **written.metrics,
                "output_dir": str(written.output_dir),
            }
            summary.runs.append(written)
            summary.rows.append(row)
        summary.write_outputs()
        return summary

    def _cases(self, factors: tuple[Sweep | MonteCarlo, ...]) -> list[tuple[ScenarioSpec, dict[str, Any]]]:
        sweeps: list[Sweep] = []
        monte_carlo: MonteCarlo | None = None
        for factor in factors:
            if isinstance(factor, Sweep):
                sweeps.append(factor)
            elif isinstance(factor, MonteCarlo):
                if monte_carlo is not None:
                    raise ValueError("only one Monte Carlo factor is supported")
                monte_carlo = factor
            else:
                raise TypeError(f"unsupported study factor: {type(factor).__name__}")

        if monte_carlo is not None and any(sweep.path == monte_carlo.path for sweep in sweeps):
            raise ValueError(f"cannot sweep and Monte Carlo over the same path: {monte_carlo.path}")

        if not sweeps and monte_carlo is None:
            return [(self.scenario, {})]

        cases = []
        sweep_products = product(*(sweep.values for sweep in sweeps)) if sweeps else [()]
        sample_indices = range(monte_carlo.samples) if monte_carlo is not None else [None]
        for values in sweep_products:
            for sample_index in sample_indices:
                mapping = scenario_to_mapping(self.scenario)
                parameters = {}
                for sweep, value in zip(sweeps, values):
                    set_mapping_path(mapping, sweep.path, value)
                    parameters[sweep.path] = value
                if monte_carlo is not None and sample_index is not None:
                    sample_seed = monte_carlo.seed + sample_index
                    set_mapping_path(mapping, monte_carlo.path, sample_seed)
                    parameters[monte_carlo.path] = sample_seed
                    parameters["monte_carlo.sample"] = sample_index
                cases.append((scenario_from_mapping(mapping), parameters))
        return cases
