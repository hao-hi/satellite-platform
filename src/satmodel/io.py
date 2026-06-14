"""Portable result-writing helpers for lightweight platform runs."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from satmodel._version import __version__
from satmodel.config.schema import ScenarioSpec, scenario_to_mapping
from satmodel.types import ReferenceAttitude, SimulationResult


def _json_default(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"object of type {type(value).__name__} is not JSON serializable")


@dataclass
class WrittenResult:
    """Paths and summary metrics produced by a result writer."""

    output_dir: Path
    metrics: dict[str, float]
    accepted: bool = True
    failed_acceptance: tuple[str, ...] = ()
    paths: dict[str, Path] = field(default_factory=dict)


class ResultWriter:
    """Write a SimulationResult into a small reproducible run directory."""

    def __init__(self, output_dir: str | Path | None = None):
        self.output_dir = None if output_dir is None else Path(output_dir)

    def write(
        self,
        result: SimulationResult,
        spec: ScenarioSpec,
        *,
        run_id: str = "run_000",
        output_dir: str | Path | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> WrittenResult:
        root = Path(output_dir) if output_dir is not None else self.output_dir
        if root is None:
            root = Path(spec.outputs.root)
        root.mkdir(parents=True, exist_ok=True)
        metrics = result.metrics(ReferenceAttitude(result.reference_quaternion))
        accepted, failed_acceptance = evaluate_acceptance(spec, metrics)
        paths: dict[str, Path] = {}
        if spec.outputs.save_manifest_json:
            paths["manifest"] = self._write_manifest(
                root / "manifest.json",
                result,
                spec,
                run_id,
                metrics,
                parameters,
                accepted,
                failed_acceptance,
            )
        if spec.outputs.save_metrics_csv:
            paths["metrics"] = self._write_metrics(root / "metrics.csv", spec, run_id, metrics, parameters, accepted, failed_acceptance)
        if spec.outputs.save_time_history_csv:
            paths["time_history"] = self._write_time_history(root / "time_history.csv", result)
        if spec.outputs.save_events_csv:
            paths["events"] = self._write_events(root / "events.csv", spec)
        if spec.outputs.save_markdown_report:
            paths["report"] = self._write_report(root / "README.md", spec, run_id, metrics, paths, parameters, accepted, failed_acceptance)
        return WrittenResult(output_dir=root, metrics=metrics, accepted=accepted, failed_acceptance=failed_acceptance, paths=paths)

    @staticmethod
    def _write_manifest(
        path: Path,
        result: SimulationResult,
        spec: ScenarioSpec,
        run_id: str,
        metrics: dict[str, float],
        parameters: dict[str, Any] | None,
        accepted: bool,
        failed_acceptance: tuple[str, ...],
    ) -> Path:
        payload = {
            "manifest_version": 1,
            "run_id": run_id,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "satmodel_version": __version__,
            "scenario": scenario_to_mapping(spec),
            "parameters": {} if parameters is None else dict(parameters),
            "metrics": metrics,
            "acceptance": {
                "accepted": accepted,
                "failed": list(failed_acceptance),
            },
            "samples": int(result.time.size),
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default) + "\n", encoding="utf-8")
        return path

    @staticmethod
    def _write_metrics(
        path: Path,
        spec: ScenarioSpec,
        run_id: str,
        metrics: dict[str, float],
        parameters: dict[str, Any] | None,
        accepted: bool,
        failed_acceptance: tuple[str, ...],
    ) -> Path:
        parameter_values = {} if parameters is None else dict(parameters)
        row = {
            "run_id": run_id,
            "scenario": spec.metadata.name,
            "seed": spec.time.seed,
            "accepted": accepted,
            "failed_acceptance": ";".join(failed_acceptance),
            **{f"param_{key}": value for key, value in parameter_values.items()},
            **metrics,
        }
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row.keys()))
            writer.writeheader()
            writer.writerow(row)
        return path

    @staticmethod
    def _write_time_history(path: Path, result: SimulationResult) -> Path:
        fieldnames = [
            "time_s",
            "attitude_error_deg",
            "true_qw",
            "true_qx",
            "true_qy",
            "true_qz",
            "estimated_qw",
            "estimated_qx",
            "estimated_qy",
            "estimated_qz",
            "reference_qw",
            "reference_qx",
            "reference_qy",
            "reference_qz",
            "omega_x_rad_s",
            "omega_y_rad_s",
            "omega_z_rad_s",
            "commanded_torque_x_nm",
            "commanded_torque_y_nm",
            "commanded_torque_z_nm",
            "applied_torque_x_nm",
            "applied_torque_y_nm",
            "applied_torque_z_nm",
            "disturbance_torque_x_nm",
            "disturbance_torque_y_nm",
            "disturbance_torque_z_nm",
            "wheel_speed_norm_rad_s",
            "wheel_momentum_fraction",
            "allocation_error_norm_nm",
        ]
        disturbance_term_names = sorted(result.disturbance_torque_terms)
        fieldnames.extend(f"{name}_torque_norm_nm" for name in disturbance_term_names)
        error = result.attitude_error_deg
        reference_track = result.reference_track()
        wheel_speeds = result.wheel_speeds_rad_s
        wheel_momentum = result.wheel_momentum_nms
        wheel_capacity = result.wheel_momentum_capacity_nms
        allocation_error = result.wheel_allocation_error_nm
        disturbance_terms = {
            name: np.asarray(track, dtype=float)
            for name, track in result.disturbance_torque_terms.items()
        }
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for index, time_s in enumerate(result.time):
                wheel_speed_norm = ""
                wheel_momentum_fraction = ""
                allocation_error_norm = ""
                disturbance_term_norms = {}
                if wheel_speeds.size and index < len(wheel_speeds):
                    wheel_speed_norm = float(np.linalg.norm(wheel_speeds[index]))
                if wheel_momentum.size and wheel_capacity.size and index < len(wheel_momentum) and index < len(wheel_capacity):
                    fraction = np.abs(wheel_momentum[index]) / np.maximum(np.abs(wheel_capacity[index]), 1e-12)
                    wheel_momentum_fraction = float(np.max(fraction))
                if allocation_error.size and index < len(allocation_error):
                    allocation_error_norm = float(np.linalg.norm(allocation_error[index]))
                for name in disturbance_term_names:
                    track = disturbance_terms.get(name)
                    value = ""
                    if track is not None and track.size and index < len(track):
                        value = float(np.linalg.norm(track[index]))
                    disturbance_term_norms[f"{name}_torque_norm_nm"] = value
                writer.writerow(
                    {
                        "time_s": float(time_s),
                        "attitude_error_deg": float(error[index]),
                        "true_qw": float(result.true_quaternion[index, 0]),
                        "true_qx": float(result.true_quaternion[index, 1]),
                        "true_qy": float(result.true_quaternion[index, 2]),
                        "true_qz": float(result.true_quaternion[index, 3]),
                        "estimated_qw": float(result.estimated_quaternion[index, 0]),
                        "estimated_qx": float(result.estimated_quaternion[index, 1]),
                        "estimated_qy": float(result.estimated_quaternion[index, 2]),
                        "estimated_qz": float(result.estimated_quaternion[index, 3]),
                        "reference_qw": float(reference_track[index, 0]),
                        "reference_qx": float(reference_track[index, 1]),
                        "reference_qy": float(reference_track[index, 2]),
                        "reference_qz": float(reference_track[index, 3]),
                        "omega_x_rad_s": float(result.true_omega[index, 0]),
                        "omega_y_rad_s": float(result.true_omega[index, 1]),
                        "omega_z_rad_s": float(result.true_omega[index, 2]),
                        "commanded_torque_x_nm": float(result.commanded_torque[index, 0]),
                        "commanded_torque_y_nm": float(result.commanded_torque[index, 1]),
                        "commanded_torque_z_nm": float(result.commanded_torque[index, 2]),
                        "applied_torque_x_nm": float(result.applied_torque[index, 0]),
                        "applied_torque_y_nm": float(result.applied_torque[index, 1]),
                        "applied_torque_z_nm": float(result.applied_torque[index, 2]),
                        "disturbance_torque_x_nm": float(result.disturbance_torque[index, 0]),
                        "disturbance_torque_y_nm": float(result.disturbance_torque[index, 1]),
                        "disturbance_torque_z_nm": float(result.disturbance_torque[index, 2]),
                        "wheel_speed_norm_rad_s": wheel_speed_norm,
                        "wheel_momentum_fraction": wheel_momentum_fraction,
                        "allocation_error_norm_nm": allocation_error_norm,
                        **disturbance_term_norms,
                    }
                )
        return path

    @staticmethod
    def _write_events(path: Path, spec: ScenarioSpec) -> Path:
        fieldnames = [
            "event_id",
            "time_s",
            "event_type",
            "target",
            "action",
            "index",
            "source",
            "payload_json",
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for index, fault in enumerate(spec.faults):
                writer.writerow(
                    {
                        "event_id": f"event_{index:03d}",
                        "time_s": float(fault.when_s),
                        "event_type": "fault",
                        "target": fault.target,
                        "action": fault.action,
                        "index": "" if fault.index is None else int(fault.index),
                        "source": "ScenarioSpec.faults",
                        "payload_json": json.dumps(
                            {
                                "target": fault.target,
                                "action": fault.action,
                                "index": fault.index,
                                "when_s": fault.when_s,
                            },
                            ensure_ascii=False,
                        ),
                    }
                )
        return path

    @staticmethod
    def _write_report(
        path: Path,
        spec: ScenarioSpec,
        run_id: str,
        metrics: dict[str, float],
        paths: dict[str, Path],
        parameters: dict[str, Any] | None,
        accepted: bool,
        failed_acceptance: tuple[str, ...],
    ) -> Path:
        lines = [
            f"# {spec.metadata.name}",
            "",
            spec.metadata.description or "Generated satmodel scenario report.",
            "",
            "## Run",
            "",
            f"- Run ID: `{run_id}`",
            f"- Duration: `{spec.time.duration_s:g} s`",
            f"- Step size: `{spec.time.dt_s:g} s`",
            f"- Seed: `{spec.time.seed}`",
            f"- System builder: `{spec.system.builder}`",
            f"- Controller: `{spec.system.controller}`",
            f"- Accepted: `{accepted}`",
        ]
        if failed_acceptance:
            lines.extend(["", "## Failed Acceptance", ""])
            lines.extend(f"- `{item}`" for item in failed_acceptance)
        if parameters:
            lines.extend(["", "## Parameters", ""])
            lines.extend(f"- `{key}`: `{value}`" for key, value in parameters.items())
        lines.extend(
            [
                "",
                "## Metrics",
                "",
                "| Metric | Value |",
                "| --- | --- |",
            ]
        )
        for key, value in metrics.items():
            lines.append(f"| `{key}` | `{value:.6g}` |")
        if paths:
            lines.extend(["", "## Files", ""])
            lines.extend(f"- `{name}`: `{item.name}`" for name, item in paths.items())
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path


def evaluate_acceptance(spec: ScenarioSpec, metrics: dict[str, float]) -> tuple[bool, tuple[str, ...]]:
    """Evaluate optional acceptance thresholds against standard metrics."""

    checks = {
        "max_final_error_deg": ("final_error_deg", spec.acceptance.max_final_error_deg),
        "max_rms_error_deg": ("rms_error_deg", spec.acceptance.max_rms_error_deg),
        "max_peak_torque_nm": ("peak_torque_nm", spec.acceptance.max_peak_torque_nm),
        "max_effort_nms": ("effort_nms", spec.acceptance.max_effort_nms),
    }
    failed = []
    for acceptance_name, (metric_name, limit) in checks.items():
        if limit is None:
            continue
        value = float(metrics[metric_name])
        if value > float(limit):
            failed.append(f"{metric_name}={value:.6g} > {acceptance_name}={float(limit):.6g}")
    return not failed, tuple(failed)
