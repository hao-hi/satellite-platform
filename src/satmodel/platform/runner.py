"""Experiment runner implementation."""

from __future__ import annotations

from itertools import product
from pathlib import Path
from typing import Any

from satmodel.config import ScenarioSpec, compile_scenario, scenario_from_mapping, scenario_to_mapping
from satmodel.io import ResultWriter
from satmodel.platform.plan import ExperimentPlan, experiment_plan_from_mapping, load_experiment_plan
from satmodel.platform.records import ExperimentRecord, ExperimentSummary
from satmodel.platform.utils import set_mapping_path
from satmodel.system import ScenarioRunner


class ExperimentRunner:
    """Run a platform experiment plan using the current satmodel runtime."""

    def __init__(self, plan: ExperimentPlan | dict[str, Any] | str | Path, output_dir: str | Path | None = None):
        if isinstance(plan, ExperimentPlan):
            self.plan = plan
        elif isinstance(plan, dict):
            self.plan = experiment_plan_from_mapping(plan)
        else:
            self.plan = load_experiment_plan(plan)
        self.output_dir = self.plan.output_root(output_dir)

    def run(self) -> ExperimentSummary:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        cases = self._cases()
        summary = ExperimentSummary(output_dir=self.output_dir, plan=self.plan)
        for index, (spec, parameters) in enumerate(cases):
            run_id = f"run_{index:03d}"
            run_dir = self.output_dir if len(cases) == 1 else self.output_dir / run_id
            compiled = compile_scenario(spec)
            result = ScenarioRunner(compiled.system).run(compiled.config)
            written = ResultWriter(run_dir).write(result, spec, run_id=run_id, parameters=parameters)
            record = ExperimentRecord(
                run_id=run_id,
                scenario=spec.metadata.name,
                seed=spec.time.seed,
                system_builder=spec.system.builder,
                controller=spec.system.controller,
                environment=spec.system.environment,
                fault_count=len(spec.faults),
                accepted=written.accepted,
                failed_acceptance=";".join(written.failed_acceptance),
                parameters=parameters,
                metrics=written.metrics,
                output_dir=written.output_dir,
                written=written,
            )
            summary.records.append(record)
            summary.runs.append(written)
            summary.rows.append(record.to_row())
        summary.write_outputs()
        return summary

    def validate(self) -> list[tuple[ScenarioSpec, dict[str, Any]]]:
        cases = self._cases()
        for spec, _parameters in cases:
            compile_scenario(spec)
        return cases

    def _cases(self) -> list[tuple[ScenarioSpec, dict[str, Any]]]:
        if not self.plan.sweeps and self.plan.monte_carlo is None:
            return [(self._scenario_with_plan_overrides(self.plan.scenario), {})]

        cases = []
        sweep_products = product(*(sweep.values for sweep in self.plan.sweeps)) if self.plan.sweeps else [()]
        sample_indices = range(self.plan.monte_carlo.samples) if self.plan.monte_carlo is not None else [None]
        for values in sweep_products:
            for sample_index in sample_indices:
                mapping = scenario_to_mapping(self._scenario_with_plan_overrides(self.plan.scenario))
                parameters = {}
                for sweep, value in zip(self.plan.sweeps, values):
                    set_mapping_path(mapping, sweep.path, value)
                    parameters[sweep.path] = value
                if self.plan.monte_carlo is not None and sample_index is not None:
                    seed = self.plan.monte_carlo.seed
                    sample_seed = (self.plan.scenario.time.seed if seed is None else seed) + sample_index
                    set_mapping_path(mapping, self.plan.monte_carlo.path, sample_seed)
                    parameters[self.plan.monte_carlo.path] = sample_seed
                    parameters["monte_carlo.sample"] = sample_index
                cases.append((scenario_from_mapping(mapping), parameters))
        return cases

    def _scenario_with_plan_overrides(self, scenario: ScenarioSpec) -> ScenarioSpec:
        mapping = scenario_to_mapping(scenario)
        if self.plan.acceptance:
            mapping.setdefault("acceptance", {}).update(self.plan.acceptance)
        return scenario_from_mapping(mapping)
