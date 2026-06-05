"""Project-level platform facade."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from satmodel.platform.plan import ExperimentPlan
from satmodel.platform.records import ExperimentSummary
from satmodel.platform.runner import ExperimentRunner


class PlatformProject:
    """Project-level facade for running experiment plans in a workspace."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.scenario_dir = self.root / "scenarios"
        self.result_dir = self.root / "results"

    def run(self, plan: ExperimentPlan | dict[str, Any] | str | Path) -> ExperimentSummary:
        experiment_plan = plan if isinstance(plan, ExperimentPlan) else ExperimentRunner(plan).plan
        output = experiment_plan.output_root(self.result_dir / experiment_plan.name)
        return ExperimentRunner(experiment_plan, output_dir=output).run()
