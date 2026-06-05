"""Command-line entry points for lightweight platform workflows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from satmodel.config import load_scenario, scenario_from_mapping, scenario_to_mapping
from satmodel.config.compiler import compile_scenario
from satmodel.studies import MonteCarlo, StudyRunner, Sweep, set_mapping_path


def _parse_value(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _parse_assignment(text: str) -> tuple[str, object]:
    if "=" not in text:
        raise argparse.ArgumentTypeError("assignments must use PATH=VALUE")
    path, raw_value = text.split("=", 1)
    if not path.strip():
        raise argparse.ArgumentTypeError("assignment path must be non-empty")
    return path.strip(), _parse_value(raw_value)


def _parse_sweep(text: str) -> Sweep:
    path, raw_value = _parse_assignment(text)
    if isinstance(raw_value, list):
        values = raw_value
    else:
        values = [_parse_value(item.strip()) for item in str(raw_value).split(",") if item.strip()]
    if not values:
        raise argparse.ArgumentTypeError("sweep values must be non-empty")
    return Sweep(path, values)


def _positive_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return value


def _scenario_with_overrides(path: str, overrides) -> object:
    spec = load_scenario(path)
    mapping = scenario_to_mapping(spec)
    for item_path, value in overrides:
        set_mapping_path(mapping, item_path, value)
    return scenario_from_mapping(mapping)


def _add_common_args(parser: argparse.ArgumentParser):
    parser.add_argument("scenario", help="Path to a .json, .yaml, or .yml scenario file")
    parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        type=_parse_assignment,
        default=[],
        metavar="PATH=VALUE",
        help="Override one scenario field before processing; VALUE is parsed as JSON when possible",
    )
    parser.add_argument(
        "--sweep",
        dest="sweeps",
        action="append",
        type=_parse_sweep,
        default=[],
        metavar="PATH=V1,V2",
        help="Validate or run a Cartesian sweep over one scenario field; values are parsed as JSON when possible",
    )
    parser.add_argument(
        "--monte-carlo",
        dest="monte_carlo",
        type=_positive_int,
        default=None,
        metavar="N",
        help="Validate or run N reproducible Monte Carlo samples by varying time.seed",
    )
    parser.add_argument(
        "--monte-carlo-seed",
        dest="monte_carlo_seed",
        type=int,
        default=None,
        metavar="SEED",
        help="Base seed for --monte-carlo; defaults to the scenario time.seed after overrides",
    )


def _study_factors(spec, args):
    factors = list(args.sweeps)
    if args.monte_carlo is not None:
        seed = args.monte_carlo_seed if args.monte_carlo_seed is not None else spec.time.seed
        factors.append(MonteCarlo(args.monte_carlo, seed=seed))
    return tuple(factors)


def run_scenario_main(argv=None) -> None:
    """Run a JSON/YAML scenario file and write the configured result directory."""

    parser = argparse.ArgumentParser(
        prog="satmodel-run-scenario",
        description="Run a satmodel ScenarioSpec file and write lightweight platform outputs.",
    )
    _add_common_args(parser)
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Override the scenario outputs.root directory",
    )
    args = parser.parse_args(argv)

    spec = _scenario_with_overrides(args.scenario, args.overrides)
    summary = StudyRunner(spec, output_dir=args.output).run(*_study_factors(spec, args))
    rows = summary.metrics_table()
    print(f"Output: {Path(summary.output_dir)}")
    if rows:
        row = rows[0]
        print(f"Runs: {len(rows)}")
        print(f"Final error deg: {float(row['final_error_deg']):.6g}")
        print(f"RMS error deg: {float(row['rms_error_deg']):.6g}")


def validate_scenario_main(argv=None) -> None:
    """Validate and compile a scenario without running a simulation."""

    parser = argparse.ArgumentParser(
        prog="satmodel-validate-scenario",
        description="Validate a satmodel ScenarioSpec file without running it.",
    )
    _add_common_args(parser)
    args = parser.parse_args(argv)

    spec = _scenario_with_overrides(args.scenario, args.overrides)
    cases = StudyRunner(spec)._cases(_study_factors(spec, args))
    for case_spec, _parameters in cases:
        compile_scenario(case_spec)
    print(f"Valid scenario: {spec.metadata.name}")
    print(f"Runs: {len(cases)}")
    print(f"Duration s: {spec.time.duration_s:g}")
    print(f"Step size s: {spec.time.dt_s:g}")
    print(f"System: {spec.system.builder}")
    print(f"Controller: {spec.system.controller}")
    print(f"Environment: {spec.system.environment}")


def main(argv=None) -> None:
    """Default CLI alias."""

    return run_scenario_main(argv)


if __name__ == "__main__":
    main()
