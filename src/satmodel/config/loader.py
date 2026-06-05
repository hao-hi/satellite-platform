"""Scenario file loading helpers."""

from __future__ import annotations

import json
from pathlib import Path

from satmodel.config.schema import ScenarioSpec, scenario_from_mapping


def load_scenario(path: str | Path) -> ScenarioSpec:
    """Load a scenario from JSON, or YAML when PyYAML is installed."""

    scenario_path = Path(path)
    suffix = scenario_path.suffix.lower()
    text = scenario_path.read_text(encoding="utf-8")
    if suffix == ".json":
        data = json.loads(text)
    elif suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:
            raise ImportError("YAML scenario files require the optional PyYAML package") from exc
        data = yaml.safe_load(text)
    else:
        raise ValueError("scenario files must use .json, .yaml, or .yml")
    if not isinstance(data, dict):
        raise ValueError("scenario file must contain a mapping at the top level")
    return scenario_from_mapping(data)
