"""Experiment-level report builders."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from satmodel._version import __version__


class ReportBuilder:
    """Build experiment-level CSV, JSON, and Markdown outputs."""

    def __init__(self, summary):
        self.summary = summary

    def acceptance_summary(self) -> dict[str, int | float]:
        rows = self.summary.rows
        run_count = len(rows)
        accepted_count = sum(1 for row in rows if bool(row.get("accepted", True)))
        failed_count = run_count - accepted_count
        acceptance_rate = accepted_count / run_count if run_count else 0.0
        return {
            "run_count": run_count,
            "accepted_count": accepted_count,
            "failed_count": failed_count,
            "acceptance_rate": acceptance_rate,
        }

    def best_row(self, metric: str = "final_error_deg") -> dict[str, Any] | None:
        if not self.summary.rows:
            return None
        candidates = [row for row in self.summary.rows if metric in row]
        if not candidates:
            return dict(self.summary.rows[0])
        return dict(min(candidates, key=lambda row: self._numeric_value(row.get(metric), default=float("inf"))))

    def worst_row(self, metric: str = "final_error_deg") -> dict[str, Any] | None:
        if not self.summary.rows:
            return None
        candidates = [row for row in self.summary.rows if metric in row]
        if not candidates:
            return dict(self.summary.rows[-1])
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
            if any(self._is_number(row.get(name)) for row in self.summary.rows):
                columns.append(name)
        return columns

    def write_metrics_csv(self, filename: str = "summary_metrics.csv") -> Path:
        path = self.summary.output_dir / filename
        fieldnames = self._fieldnames()
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in self.summary.rows:
                writer.writerow({key: self._cell(row.get(key, "")) for key in fieldnames})
        return path

    def write_study_manifest(self, filename: str = "study_manifest.json") -> Path:
        path = self.summary.output_dir / filename
        payload = {
            "manifest_version": 1,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "satmodel_version": __version__,
            "run_count": len(self.summary.rows),
            "runs": self.summary.rows,
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        return path

    def write_experiment_manifest(self, filename: str = "experiment_manifest.json") -> Path:
        path = self.summary.output_dir / filename
        payload = {
            "manifest_version": 1,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "satmodel_version": __version__,
            "experiment": {} if self.summary.plan is None else self.summary.plan.to_mapping(),
            "run_count": len(self.summary.rows),
            "runs": self.summary.rows,
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        return path

    def write_index(self, filename: str = "index.json") -> Path:
        path = self.summary.output_dir / filename
        acceptance = self.acceptance_summary()
        best = self.best_row()
        runtime_file = "runtime_schedule.json" if self._has_runtime() else None
        mode_file = "mode_timeline.json" if self._has_mission() else None
        payload = {
            "index_version": 1,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "satmodel_version": __version__,
            **acceptance,
            "best_run_id": None if best is None else best.get("run_id"),
            "best_output_dir": None if best is None else best.get("output_dir"),
            "metric_columns": self.metric_columns(),
            "parameter_columns": self.parameter_columns(),
            "runtime_schedule": runtime_file,
            "mode_timeline": mode_file,
            "runs": self.summary.rows,
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        return path

    def write_markdown(self, filename: str = "README.md") -> Path:
        path = self.summary.output_dir / filename
        acceptance = self.acceptance_summary()
        best = self.best_row()
        worst = self.worst_row()
        parameter_columns = self.parameter_columns()
        metric_columns = self.metric_columns()
        title = "satmodel Experiment Summary"
        if self.summary.plan is not None:
            title = f"satmodel Experiment Summary: {self.summary.plan.name}"
        lines = [
            f"# {title}",
            "",
            f"- Runs: `{len(self.summary.rows)}`",
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
        lines.extend(f"- `{column}`" for column in parameter_columns) if parameter_columns else lines.append("- None")
        lines.extend(["", "## Metrics", ""])
        lines.extend(f"- `{column}`" for column in metric_columns) if metric_columns else lines.append("- None")
        lines.extend(
            [
                "",
                "## Runs",
                "",
                "| Run | Scenario | Accepted | Final error deg | RMS error deg | Peak torque N m | Output |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for row in self.summary.rows:
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
                "- `study_manifest.json`: compatibility manifest with raw rows.",
                "- `experiment_manifest.json`: platform experiment manifest with plan metadata.",
                "- `index.json`: compact machine-readable experiment index.",
            ]
        )
        if self._has_runtime():
            lines.append("- `runtime_schedule.json`: expanded process/task/module schedule.")
        if self._has_mission():
            lines.append("- `mode_timeline.json`: mission step and mode intervals.")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def write_runtime_schedule(self, filename: str = "runtime_schedule.json") -> Path:
        path = self.summary.output_dir / filename
        plan = self.summary.plan
        runtime = None if plan is None else plan.runtime
        if runtime is None:
            raise ValueError("experiment plan has no runtime process")
        schedule = runtime.schedule(plan.scenario.time.duration_s)
        payload = {
            "schedule_version": 1,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "satmodel_version": __version__,
            "duration_s": plan.scenario.time.duration_s,
            "runtime": runtime.to_mapping(),
            "event_count": len(schedule),
            "events": schedule,
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        return path

    def write_mode_timeline(self, filename: str = "mode_timeline.json") -> Path:
        path = self.summary.output_dir / filename
        plan = self.summary.plan
        mission = None if plan is None else plan.mission
        if mission is None:
            raise ValueError("experiment plan has no mission sequence")
        payload = {
            "timeline_version": 1,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "satmodel_version": __version__,
            "duration_s": mission.duration_s,
            "mission": mission.to_mapping(),
            "timeline": mission.mode_timeline().to_mapping(),
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        return path

    def write_outputs(self) -> dict[str, Path]:
        outputs = {
            "summary_metrics": self.write_metrics_csv(),
            "study_manifest": self.write_study_manifest(),
            "experiment_manifest": self.write_experiment_manifest(),
        }
        if self._has_runtime():
            outputs["runtime_schedule"] = self.write_runtime_schedule()
        if self._has_mission():
            outputs["mode_timeline"] = self.write_mode_timeline()
        outputs["index"] = self.write_index()
        outputs["report"] = self.write_markdown()
        return outputs

    def _fieldnames(self) -> list[str]:
        names: list[str] = []
        for row in self.summary.rows:
            for key in row:
                if key not in names:
                    names.append(key)
        return names

    def _has_runtime(self) -> bool:
        return self.summary.plan is not None and self.summary.plan.runtime is not None

    def _has_mission(self) -> bool:
        return self.summary.plan is not None and self.summary.plan.mission is not None

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
