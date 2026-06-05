"""Compatibility study runner built on the platform experiment layer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from satmodel.config import ScenarioSpec, load_scenario, scenario_from_mapping
from satmodel.platform import (
    ExperimentMonteCarloSpec,
    ExperimentOutputSpec,
    ExperimentPlan,
    ExperimentRunner,
    ExperimentSummary,
    ExperimentSweepSpec,
)
from satmodel.platform.core import set_mapping_path


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


class StudyRunner:
    """Backward-compatible runner for one scenario, sweeps, and Monte Carlo samples."""

    def __init__(self, scenario: ScenarioSpec | dict[str, Any] | str | Path, output_dir: str | Path | None = None):
        if isinstance(scenario, ScenarioSpec):
            self.scenario = scenario
        elif isinstance(scenario, dict):
            self.scenario = scenario_from_mapping(scenario)
        else:
            self.scenario = load_scenario(scenario)
        self.output_dir = Path(output_dir) if output_dir is not None else Path(self.scenario.outputs.root)

    def run(self, *factors: Sweep | MonteCarlo) -> ExperimentSummary:
        return ExperimentRunner(self._plan(factors), output_dir=self.output_dir).run()

    def _cases(self, factors: tuple[Sweep | MonteCarlo, ...]) -> list[tuple[ScenarioSpec, dict[str, Any]]]:
        return ExperimentRunner(self._plan(factors), output_dir=self.output_dir)._cases()

    def _plan(self, factors: tuple[Sweep | MonteCarlo, ...]) -> ExperimentPlan:
        sweeps: list[ExperimentSweepSpec] = []
        monte_carlo: ExperimentMonteCarloSpec | None = None
        for factor in factors:
            if isinstance(factor, Sweep):
                sweeps.append(ExperimentSweepSpec(factor.path, factor.values))
            elif isinstance(factor, MonteCarlo):
                if monte_carlo is not None:
                    raise ValueError("only one Monte Carlo factor is supported")
                monte_carlo = ExperimentMonteCarloSpec(factor.samples, seed=factor.seed, path=factor.path)
            else:
                raise TypeError(f"unsupported study factor: {type(factor).__name__}")
        return ExperimentPlan(
            metadata={"name": self.scenario.metadata.name},
            scenario=self.scenario,
            sweeps=tuple(sweeps),
            monte_carlo=monte_carlo,
            outputs=ExperimentOutputSpec(root=str(self.output_dir)),
        )


StudySummary = ExperimentSummary
