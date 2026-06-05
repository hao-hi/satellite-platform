"""Experiment record and summary objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from satmodel.io import WrittenResult
from satmodel.platform.plan import ExperimentPlan


@dataclass
class ExperimentRecord:
    """A normalized row describing one completed experiment run."""

    run_id: str
    scenario: str
    seed: int
    system_builder: str
    controller: str
    environment: str
    fault_count: int
    accepted: bool
    failed_acceptance: str
    parameters: dict[str, Any]
    metrics: dict[str, float]
    output_dir: Path
    written: WrittenResult

    def to_row(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "scenario": self.scenario,
            "seed": self.seed,
            "system_builder": self.system_builder,
            "controller": self.controller,
            "environment": self.environment,
            "fault_count": self.fault_count,
            "accepted": self.accepted,
            "failed_acceptance": self.failed_acceptance,
            **{f"param_{key}": value for key, value in self.parameters.items()},
            **self.metrics,
            "output_dir": str(self.output_dir),
        }


@dataclass
class ExperimentSummary:
    """Summary and report-writing facade for one experiment run."""

    output_dir: Path
    plan: ExperimentPlan | None = None
    records: list[ExperimentRecord] = field(default_factory=list)
    runs: list[WrittenResult] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)

    def metrics_table(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.rows]

    def acceptance_summary(self) -> dict[str, int | float]:
        from satmodel.platform.reporting import ReportBuilder

        return ReportBuilder(self).acceptance_summary()

    def best_row(self, metric: str = "final_error_deg") -> dict[str, Any] | None:
        from satmodel.platform.reporting import ReportBuilder

        return ReportBuilder(self).best_row(metric)

    def worst_row(self, metric: str = "final_error_deg") -> dict[str, Any] | None:
        from satmodel.platform.reporting import ReportBuilder

        return ReportBuilder(self).worst_row(metric)

    def parameter_columns(self) -> list[str]:
        from satmodel.platform.reporting import ReportBuilder

        return ReportBuilder(self).parameter_columns()

    def metric_columns(self) -> list[str]:
        from satmodel.platform.reporting import ReportBuilder

        return ReportBuilder(self).metric_columns()

    def write_metrics_csv(self, filename: str = "summary_metrics.csv") -> Path:
        from satmodel.platform.reporting import ReportBuilder

        return ReportBuilder(self).write_metrics_csv(filename)

    def write_manifest(self, filename: str = "study_manifest.json") -> Path:
        from satmodel.platform.reporting import ReportBuilder

        return ReportBuilder(self).write_study_manifest(filename)

    def write_experiment_manifest(self, filename: str = "experiment_manifest.json") -> Path:
        from satmodel.platform.reporting import ReportBuilder

        return ReportBuilder(self).write_experiment_manifest(filename)

    def write_index(self, filename: str = "index.json") -> Path:
        from satmodel.platform.reporting import ReportBuilder

        return ReportBuilder(self).write_index(filename)

    def write_markdown(self, filename: str = "README.md") -> Path:
        from satmodel.platform.reporting import ReportBuilder

        return ReportBuilder(self).write_markdown(filename)

    def write_outputs(self) -> dict[str, Path]:
        from satmodel.platform.reporting import ReportBuilder

        return ReportBuilder(self).write_outputs()
