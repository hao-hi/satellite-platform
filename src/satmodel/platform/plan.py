"""Experiment plan schema and loading helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from satmodel.config import ScenarioSpec, load_scenario, scenario_from_mapping, scenario_to_mapping
from satmodel.platform.mission import MissionSequence, mission_sequence_from_mapping
from satmodel.platform.runtime import RuntimeProcess, runtime_process_from_mapping
from satmodel.platform.utils import reject_unknown


@dataclass(frozen=True)
class ExperimentSweepSpec:
    """One Cartesian experiment parameter dimension."""

    path: str
    values: tuple[Any, ...]

    def __init__(self, path: str, values):
        object.__setattr__(self, "path", str(path))
        object.__setattr__(self, "values", tuple(values))
        if not self.path:
            raise ValueError("sweep path must be non-empty")
        if not self.values:
            raise ValueError("sweep values must be non-empty")

    def to_mapping(self) -> dict[str, Any]:
        return {"path": self.path, "values": list(self.values)}


@dataclass(frozen=True)
class ExperimentMonteCarloSpec:
    """A reproducible Monte Carlo seed series for an experiment plan."""

    samples: int
    seed: int | None = None
    path: str = "time.seed"

    def __init__(self, samples: int, seed: int | None = None, path: str = "time.seed"):
        object.__setattr__(self, "samples", int(samples))
        object.__setattr__(self, "seed", None if seed is None else int(seed))
        object.__setattr__(self, "path", str(path))
        if self.samples <= 0:
            raise ValueError("Monte Carlo samples must be positive")
        if not self.path:
            raise ValueError("Monte Carlo seed path must be non-empty")

    def to_mapping(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"samples": self.samples, "path": self.path}
        if self.seed is not None:
            payload["seed"] = self.seed
        return payload


@dataclass
class ExperimentOutputSpec:
    """Experiment-level output settings."""

    root: str | None = None

    def __post_init__(self):
        if self.root is not None:
            self.root = str(self.root)

    def to_mapping(self) -> dict[str, Any]:
        return {} if self.root is None else {"root": self.root}


@dataclass
class ExperimentPlan:
    """Versioned v0.3 experiment contract."""

    schema_version: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)
    scenario: ScenarioSpec = field(default_factory=ScenarioSpec)
    sweeps: tuple[ExperimentSweepSpec, ...] = ()
    monte_carlo: ExperimentMonteCarloSpec | None = None
    runtime: RuntimeProcess | None = None
    mission: MissionSequence | None = None
    outputs: ExperimentOutputSpec = field(default_factory=ExperimentOutputSpec)
    acceptance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.schema_version = int(self.schema_version)
        if self.schema_version != 1:
            raise ValueError("only experiment schema_version 1 is supported")
        self.metadata = dict(self.metadata)
        if not isinstance(self.scenario, ScenarioSpec):
            self.scenario = scenario_from_mapping(dict(self.scenario))
        self.sweeps = tuple(item if isinstance(item, ExperimentSweepSpec) else sweep_from_mapping(item) for item in self.sweeps)
        if self.monte_carlo is not None and not isinstance(self.monte_carlo, ExperimentMonteCarloSpec):
            self.monte_carlo = monte_carlo_from_mapping(self.monte_carlo)
        if self.monte_carlo is not None and any(sweep.path == self.monte_carlo.path for sweep in self.sweeps):
            raise ValueError(f"cannot sweep and Monte Carlo over the same path: {self.monte_carlo.path}")
        if self.runtime is not None and not isinstance(self.runtime, RuntimeProcess):
            self.runtime = runtime_from_plan_value(self.runtime, self.scenario)
        if self.mission is not None and not isinstance(self.mission, MissionSequence):
            self.mission = mission_from_plan_value(self.mission, self.scenario)
        if not isinstance(self.outputs, ExperimentOutputSpec):
            self.outputs = ExperimentOutputSpec(**dict(self.outputs))
        self.acceptance = dict(self.acceptance)

    @property
    def name(self) -> str:
        return str(self.metadata.get("name") or self.scenario.metadata.name or "experiment")

    def output_root(self, override: str | Path | None = None) -> Path:
        if override is not None:
            return Path(override)
        if self.outputs.root:
            return Path(self.outputs.root)
        return Path(self.scenario.outputs.root)

    def to_mapping(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "metadata": dict(self.metadata),
            "scenario": scenario_to_mapping(self.scenario),
            "sweeps": [item.to_mapping() for item in self.sweeps],
            "outputs": self.outputs.to_mapping(),
            "acceptance": dict(self.acceptance),
        }
        if self.monte_carlo is not None:
            payload["monte_carlo"] = self.monte_carlo.to_mapping()
        if self.runtime is not None:
            payload["runtime"] = self.runtime.to_mapping()
        if self.mission is not None:
            payload["mission"] = self.mission.to_mapping()
        return payload


def experiment_plan_from_mapping(values: dict[str, Any], *, base_dir: str | Path | None = None) -> ExperimentPlan:
    """Build a strict experiment plan from a plain mapping."""

    data = dict(values)
    reject_unknown(
        "experiment",
        data,
        {"schema_version", "metadata", "scenario", "sweeps", "monte_carlo", "runtime", "mission", "outputs", "acceptance"},
    )
    if "metadata" in data:
        reject_unknown("metadata", dict(data["metadata"]), {"name", "description", "tags"})
    if "outputs" in data:
        reject_unknown("outputs", dict(data["outputs"]), {"root"})
    if "monte_carlo" in data and data["monte_carlo"] is not None:
        reject_unknown("monte_carlo", dict(data["monte_carlo"]), {"samples", "seed", "path"})
    for index, sweep in enumerate(data.get("sweeps", ())):
        reject_unknown(f"sweeps[{index}]", dict(sweep), {"path", "values"})

    scenario_value = data.get("scenario")
    if scenario_value is None:
        raise ValueError("experiment.scenario is required")
    scenario = scenario_from_plan_value(scenario_value, base_dir=base_dir)
    return ExperimentPlan(
        schema_version=data.get("schema_version", 1),
        metadata=data.get("metadata", {}),
        scenario=scenario,
        sweeps=tuple(sweep_from_mapping(item) for item in data.get("sweeps", ())),
        monte_carlo=None if data.get("monte_carlo") is None else monte_carlo_from_mapping(data["monte_carlo"]),
        runtime=None if data.get("runtime") is None else runtime_from_plan_value(data["runtime"], scenario),
        mission=None if data.get("mission") is None else mission_from_plan_value(data["mission"], scenario),
        outputs=ExperimentOutputSpec(**dict(data.get("outputs", {}))),
        acceptance=dict(data.get("acceptance", {})),
    )


def load_experiment_plan(path: str | Path) -> ExperimentPlan:
    """Load a JSON/YAML experiment plan."""

    plan_path = Path(path)
    text = plan_path.read_text(encoding="utf-8")
    suffix = plan_path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError("YAML experiment plans require PyYAML") from exc
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("experiment plan must be a mapping")
    return experiment_plan_from_mapping(data, base_dir=plan_path.parent)


def scenario_from_plan_value(value, *, base_dir: str | Path | None) -> ScenarioSpec:
    """Load an experiment scenario from an inline mapping or relative path."""

    if isinstance(value, ScenarioSpec):
        return value
    if isinstance(value, str):
        path = Path(value)
        if base_dir is not None and not path.is_absolute():
            path = Path(base_dir) / path
        return load_scenario(path)
    if isinstance(value, dict):
        return scenario_from_mapping(value)
    raise TypeError("experiment.scenario must be a mapping or scenario file path")


def sweep_from_mapping(value) -> ExperimentSweepSpec:
    if isinstance(value, ExperimentSweepSpec):
        return value
    data = dict(value)
    return ExperimentSweepSpec(data["path"], data["values"])


def monte_carlo_from_mapping(value) -> ExperimentMonteCarloSpec:
    if isinstance(value, ExperimentMonteCarloSpec):
        return value
    data = dict(value)
    return ExperimentMonteCarloSpec(data["samples"], seed=data.get("seed"), path=data.get("path", "time.seed"))


def runtime_from_plan_value(value, scenario: ScenarioSpec) -> RuntimeProcess:
    if isinstance(value, RuntimeProcess):
        return value
    if isinstance(value, str):
        return runtime_process_from_mapping({"template": value, "dt_s": scenario.time.dt_s})
    data = dict(value)
    if "template" in data and "dt_s" not in data:
        data["dt_s"] = scenario.time.dt_s
    return runtime_process_from_mapping(data)


def mission_from_plan_value(value, scenario: ScenarioSpec) -> MissionSequence:
    if isinstance(value, MissionSequence):
        return value
    if isinstance(value, str):
        return mission_sequence_from_mapping({"template": "single_mode", "mode": value, "duration_s": scenario.time.duration_s})
    data = dict(value)
    if "template" in data and "duration_s" not in data:
        data["duration_s"] = scenario.time.duration_s
    return mission_sequence_from_mapping(data)
