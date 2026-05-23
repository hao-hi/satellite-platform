"""Reproducible reaction-wheel simulation study with paper-style outputs."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from satmodel import (
    CubeSatPhysicalConfig,
    ReactionWheelArrayConfig,
    ReactionWheelConfig,
    ScenarioRunner,
    SimulationConfig,
    build_cubesat_reaction_wheel_system,
)
from satmodel.math import quat_angle_error_deg


AXES_PYRAMID_4RW = np.array(
    [
        [1.0, 1.0, 1.0],
        [1.0, -1.0, -1.0],
        [-1.0, 1.0, -1.0],
        [-1.0, -1.0, 1.0],
    ],
    dtype=float,
)


@dataclass(frozen=True)
class StudyCase:
    """One deterministic simulation case in the reaction-wheel study."""

    name: str
    label: str
    physical_config: CubeSatPhysicalConfig
    disabled_wheels: tuple[int, ...] = ()
    seed: int = 42


def _pyramid_physical_config(
    *,
    allocation: str = "bounded_pinv",
    max_torque_nm: float = 0.007,
    initial_speeds_rad_s=None,
    momentum_gain: float = 0.0,
) -> CubeSatPhysicalConfig:
    base = CubeSatPhysicalConfig.one_unit_reaction_wheel_demo()
    speeds = np.zeros(4, dtype=float) if initial_speeds_rad_s is None else np.asarray(initial_speeds_rad_s, dtype=float)
    wheels = tuple(
        ReactionWheelConfig(
            axis,
            spin_inertia_kgm2=2.6e-5,
            max_torque_nm=max_torque_nm,
            max_speed_rad_s=8000.0 * 2.0 * np.pi / 60.0,
            initial_speed_rad_s=float(speed),
        )
        for axis, speed in zip(AXES_PYRAMID_4RW, speeds)
    )
    wheel_config = ReactionWheelArrayConfig(
        wheels,
        allocation=allocation,
        momentum_reference_rad_s=0.0,
        momentum_gain=momentum_gain,
    )
    return CubeSatPhysicalConfig(base.geometry, base.mass_properties, wheel_config)


def build_study_cases() -> list[StudyCase]:
    """Return controlled comparison cases for the paper-style study."""

    biased_speed = np.array([80.0, -60.0, 40.0, -90.0], dtype=float)
    return [
        StudyCase("nominal_4rw", "Nominal 4RW bounded allocation", _pyramid_physical_config()),
        StudyCase(
            "one_wheel_failed",
            "One wheel failed from t=0",
            _pyramid_physical_config(),
            disabled_wheels=(0,),
        ),
        StudyCase(
            "low_torque_4rw",
            "Torque-limited 4RW bounded allocation",
            _pyramid_physical_config(max_torque_nm=0.002),
        ),
        StudyCase(
            "biased_no_nullspace",
            "Biased wheels without null-space management",
            _pyramid_physical_config(initial_speeds_rad_s=biased_speed),
        ),
        StudyCase(
            "nullspace_bias",
            "Biased wheels with null-space momentum management",
            _pyramid_physical_config(
                allocation="nullspace_momentum",
                initial_speeds_rad_s=biased_speed,
                momentum_gain=0.2,
            ),
        ),
    ]


def _attitude_error_deg(result) -> np.ndarray:
    reference = result.reference_quaternion
    return np.asarray([quat_angle_error_deg(reference, q) for q in result.true_quaternion], dtype=float)


def _first_time_remaining_below(time: np.ndarray, values: np.ndarray, threshold: float) -> float:
    below = values <= float(threshold)
    for index in range(below.size):
        if bool(np.all(below[index:])):
            return float(time[index])
    return float("nan")


def _metric_row(case: StudyCase, result) -> dict[str, float | str | int]:
    error = _attitude_error_deg(result)
    torque_norm = np.linalg.norm(result.applied_torque, axis=1)
    allocation_error_norm = np.linalg.norm(result.wheel_allocation_error_nm, axis=1)
    wheel_speed_norm = np.linalg.norm(result.wheel_speeds_rad_s, axis=1)
    wheel_momentum_fraction = np.abs(result.wheel_momentum_nms) / np.maximum(
        result.wheel_momentum_capacity_nms,
        1e-15,
    )
    disturbance_norm = np.linalg.norm(result.disturbance_torque, axis=1)
    integral = getattr(np, "trapezoid", np.trapz)
    telemetry = [item for item in result.actuator_telemetry if item is not None]
    active_bound_samples = int(
        sum(np.count_nonzero(np.logical_and(~item.free_wheel_mask, item.enabled)) for item in telemetry)
    )
    rank_min = int(min(item.rank_after_failures for item in telemetry)) if telemetry else 0
    return {
        "case": case.name,
        "label": case.label,
        "initial_error_deg": float(error[0]),
        "final_error_deg": float(error[-1]),
        "rms_error_deg": float(np.sqrt(np.mean(error**2))),
        "settling_time_1deg_s": _first_time_remaining_below(result.time, error, 1.0),
        "effort_nms": float(integral(torque_norm, result.time)),
        "peak_body_torque_nm": float(np.max(torque_norm)),
        "rms_allocation_error_nm": float(np.sqrt(np.mean(allocation_error_norm**2))),
        "peak_allocation_error_nm": float(np.max(allocation_error_norm)),
        "peak_wheel_speed_rad_s": float(np.max(np.abs(result.wheel_speeds_rad_s))),
        "initial_wheel_speed_norm_rad_s": float(wheel_speed_norm[0]),
        "mean_wheel_speed_norm_rad_s": float(np.mean(wheel_speed_norm)),
        "final_wheel_speed_norm_rad_s": float(wheel_speed_norm[-1]),
        "peak_wheel_momentum_fraction": float(np.max(wheel_momentum_fraction)),
        "active_bound_samples": active_bound_samples,
        "min_rank_after_failures": rank_min,
        "peak_disturbance_torque_nm": float(np.max(disturbance_norm)),
    }


def _write_summary_csv(path: Path, rows: list[dict[str, float | str | int]]):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _write_history_csv(path: Path, histories: dict[str, object]):
    fieldnames = [
        "case",
        "time_s",
        "attitude_error_deg",
        "body_torque_norm_nm",
        "allocation_error_norm_nm",
        "wheel_speed_norm_rad_s",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for name, result in histories.items():
            error = _attitude_error_deg(result)
            torque_norm = np.linalg.norm(result.applied_torque, axis=1)
            allocation_norm = np.linalg.norm(result.wheel_allocation_error_nm, axis=1)
            speed_norm = np.linalg.norm(result.wheel_speeds_rad_s, axis=1)
            for index, time_s in enumerate(result.time):
                writer.writerow(
                    {
                        "case": name,
                        "time_s": float(time_s),
                        "attitude_error_deg": float(error[index]),
                        "body_torque_norm_nm": float(torque_norm[index]),
                        "allocation_error_norm_nm": float(allocation_norm[index]),
                        "wheel_speed_norm_rad_s": float(speed_norm[index]),
                    }
                )


def _format_float(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    value = float(value)
    return "nan" if not np.isfinite(value) else f"{value:.6g}"


def _write_markdown_report(path: Path, rows: list[dict[str, float | str | int]], *, duration: float, dt: float):
    metric_names = [
        "final_error_deg",
        "rms_error_deg",
        "settling_time_1deg_s",
        "effort_nms",
        "peak_body_torque_nm",
        "peak_allocation_error_nm",
        "peak_wheel_speed_rad_s",
        "mean_wheel_speed_norm_rad_s",
        "peak_wheel_momentum_fraction",
        "active_bound_samples",
        "min_rank_after_failures",
    ]
    lines = [
        "# Reaction-Wheel Array Simulation Study",
        "",
        "## Method",
        "",
        "This deterministic numerical study follows a paper-style simulation layout: a controlled baseline, fault case, actuator-limit case, and paired redundancy-management ablation are run with the same rigid 1U CubeSat plant, PD attitude controller, composable LEO environment, and fixed-step integration settings.",
        "",
        f"- Duration: `{duration:g} s`",
        f"- Step size: `{dt:g} s`",
        "- Initial attitude error: `35 deg` from the package default initial state",
        "- Environment: default demo LEO environment with gravity-gradient, residual magnetic, aerodynamic, and SRP torque effectors",
        "- Main response metrics: final/RMS attitude error, 1 deg settling time, torque effort, allocation error, wheel speed, wheel momentum utilization, active wheel-bound samples",
        "",
        "## Results",
        "",
        "| Case | " + " | ".join(metric_names) + " |",
        "| --- | " + " | ".join("---" for _ in metric_names) + " |",
    ]
    for row in rows:
        lines.append(
            f"| {row['label']} | "
            + " | ".join(_format_float(row[name]) for name in metric_names)
            + " |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- The nominal 4RW case is the reference closed-loop pointing result.",
            "- The one-wheel-failed case tests whether the skewed four-wheel cluster retains reduced three-axis authority after a single disabled wheel.",
            "- The low-torque case exercises the bounded allocator: allocation error and active-bound samples should increase when requested body torque exceeds the feasible wheel envelope.",
            "- The two biased-wheel cases form an ablation pair: the null-space case uses the redundant wheel degree of freedom to manage internal wheel momentum without changing the commanded body torque.",
            "",
            "Generated files: `summary_metrics.csv`, `time_history.csv`, `attitude_error.png`, `allocation_error.png`, and `wheel_speed_norm.png`.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _plot_results(output_dir: Path, histories: dict[str, object], rows: list[dict[str, float | str | int]]):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = {row["case"]: row["label"] for row in rows}
    plot_specs = [
        ("attitude_error.png", "Attitude error", "error deg", lambda result: _attitude_error_deg(result)),
        (
            "allocation_error.png",
            "Allocation error norm",
            "N m",
            lambda result: np.linalg.norm(result.wheel_allocation_error_nm, axis=1),
        ),
        (
            "wheel_speed_norm.png",
            "Wheel speed norm",
            "rad/s",
            lambda result: np.linalg.norm(result.wheel_speeds_rad_s, axis=1),
        ),
    ]
    for filename, title, ylabel, selector in plot_specs:
        fig, axis = plt.subplots(figsize=(7.2, 4.2), constrained_layout=True)
        for name, result in histories.items():
            axis.plot(result.time, selector(result), label=labels[name], linewidth=1.5)
        axis.set_title(title)
        axis.set_xlabel("time s")
        axis.set_ylabel(ylabel)
        axis.grid(True, alpha=0.3)
        axis.legend(fontsize=8)
        fig.savefig(output_dir / filename, dpi=180)
        plt.close(fig)


def run_reaction_wheel_study(
    output_dir: str | Path = "results/reaction_wheel_study",
    *,
    duration: float = 20.0,
    dt: float = 0.02,
    make_plots: bool = True,
) -> list[dict[str, float | str | int]]:
    """Run the study and write reproducible result artifacts."""

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    histories = {}
    rows = []
    for case in build_study_cases():
        system = build_cubesat_reaction_wheel_system(
            controller="pd",
            physical_config=case.physical_config,
        )
        for wheel_index in case.disabled_wheels:
            system.actuator.disable_wheel(wheel_index)
        result = ScenarioRunner(system).run(SimulationConfig(duration=duration, dt=dt, seed=case.seed))
        histories[case.name] = result
        rows.append(_metric_row(case, result))

    _write_summary_csv(output_path / "summary_metrics.csv", rows)
    _write_history_csv(output_path / "time_history.csv", histories)
    _write_markdown_report(output_path / "README.md", rows, duration=duration, dt=dt)
    if make_plots:
        _plot_results(output_path, histories, rows)

    print(f"wrote study outputs to {output_path.resolve()}")
    for row in rows:
        print(
            f"{row['case']}: final={row['final_error_deg']:.4f} deg, "
            f"rms={row['rms_error_deg']:.4f} deg, "
            f"peak_alloc={row['peak_allocation_error_nm']:.4e} N m"
        )
    return rows


def main(argv: list[str] | None = None):
    """Console entry point for the reaction-wheel study."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="results/reaction_wheel_study")
    parser.add_argument("--duration", type=float, default=20.0)
    parser.add_argument("--dt", type=float, default=0.02)
    parser.add_argument("--no-plots", action="store_true")
    args = parser.parse_args(argv)
    run_reaction_wheel_study(
        args.output,
        duration=args.duration,
        dt=args.dt,
        make_plots=not args.no_plots,
    )


if __name__ == "__main__":
    main()
