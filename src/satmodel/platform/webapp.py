"""Local browser UI for operating satmodel platform experiments."""

from __future__ import annotations

import copy
import csv
import json
import mimetypes
import os
import re
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from satmodel._version import __version__
from satmodel.config import load_scenario
from satmodel.config.compiler import compile_scenario
from satmodel.platform.dashboard import build_dashboard
from satmodel.platform.plan import experiment_plan_from_mapping, load_experiment_plan
from satmodel.platform.runner import ExperimentRunner


def _plan_asset_meta_from_mapping(path: Path, mapping: dict[str, Any]) -> dict[str, Any]:
    metadata = mapping.get("metadata") if isinstance(mapping, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    tags_raw = metadata.get("tags")
    tags = [str(tag) for tag in tags_raw] if isinstance(tags_raw, list) else []
    tags_lower = {tag.lower() for tag in tags}
    name = str(metadata.get("name") or path.stem)
    output_root = ""
    outputs = mapping.get("outputs") if isinstance(mapping, dict) else {}
    if isinstance(outputs, dict):
        output_root = str(outputs.get("root") or "")
    family = re.sub(r"_(\d+)$", "", name)
    is_library = "library" in tags_lower or "platform_library" in output_root
    is_generated = "generated" in tags_lower or "generated platform experiment" in str(metadata.get("description") or "").lower()
    is_showcase = "showcase" in name or "showcase" in family
    is_numbered_variant = family != name
    needs_consolidation = is_numbered_variant and (is_generated or is_showcase)
    if is_library:
        asset_kind = "library"
        asset_label = "标准实验资产"
    elif is_showcase and is_numbered_variant:
        asset_kind = "showcase_variant"
        asset_label = "演示副本待收敛"
    elif is_showcase:
        asset_kind = "showcase"
        asset_label = "演示入口"
    elif is_generated and is_numbered_variant:
        asset_kind = "generated_variant"
        asset_label = "生成副本待收敛"
    elif is_generated:
        asset_kind = "generated"
        asset_label = "平台生成计划"
    else:
        asset_kind = "workspace"
        asset_label = "工作区计划"
    return {
        "asset_kind": asset_kind,
        "asset_label": asset_label,
        "asset_family": family,
        "needs_consolidation": needs_consolidation,
        "tags": tags,
    }


def _summarize_experiment_assets(experiments: list[dict[str, Any]], archived_experiments: list[dict[str, Any]]) -> dict[str, Any]:
    bucket_counts = {
        "library": 0,
        "showcase": 0,
        "showcase_variant": 0,
        "generated": 0,
        "generated_variant": 0,
        "workspace": 0,
    }
    active_by_family: dict[str, list[dict[str, Any]]] = {}
    for row in experiments:
        kind = str(row.get("asset_kind") or "workspace")
        bucket_counts[kind] = bucket_counts.get(kind, 0) + 1
        family = str(row.get("asset_family") or row.get("name") or "")
        active_by_family.setdefault(family, []).append(row)
    duplication_candidates = []
    for family, rows in sorted(active_by_family.items()):
        if len(rows) < 2:
            continue
        variant_rows = [row for row in rows if row.get("needs_consolidation")]
        if not variant_rows:
            continue
        duplication_candidates.append(
            {
                "family": family,
                "count": len(rows),
                "paths": [str(row.get("path") or "") for row in rows],
                "names": [str(row.get("name") or "") for row in rows],
            }
        )
    return {
        "library_count": bucket_counts.get("library", 0),
        "showcase_count": bucket_counts.get("showcase", 0) + bucket_counts.get("showcase_variant", 0),
        "generated_count": bucket_counts.get("generated", 0) + bucket_counts.get("generated_variant", 0),
        "workspace_count": bucket_counts.get("workspace", 0),
        "needs_consolidation_count": sum(1 for row in experiments if row.get("needs_consolidation")),
        "archived_count": len(archived_experiments),
        "duplication_candidates": duplication_candidates,
        "bucket_counts": bucket_counts,
    }


def discover_workspace(root: str | Path) -> dict[str, Any]:
    """Discover scenario files, experiment plans, and result dashboards."""

    workspace = Path(root).resolve()
    scenario_dir = workspace / "scenarios"
    archive_dir = scenario_dir / "archive"
    results_dir = workspace / "results"
    scenarios: list[dict[str, Any]] = []
    experiments: list[dict[str, Any]] = []
    for path in sorted(scenario_dir.glob("*.json")) if scenario_dir.exists() else []:
        rel = _relative(path, workspace)
        stat = path.stat()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "scenario" in data:
                plan = load_experiment_plan(path)
                asset_meta = _plan_asset_meta_from_mapping(path, data)
                experiments.append(
                    {
                        "name": plan.name,
                        "path": rel,
                        "scenario": plan.scenario.metadata.name,
                        "sweeps": len(plan.sweeps),
                        "monte_carlo_samples": 0 if plan.monte_carlo is None else plan.monte_carlo.samples,
                        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                        "updated_ts": stat.st_mtime,
                        **asset_meta,
                    }
                )
            else:
                scenarios.append(
                    {
                        "name": data.get("metadata", {}).get("name", path.stem) if isinstance(data, dict) else path.stem,
                        "path": rel,
                        "description": data.get("metadata", {}).get("description") if isinstance(data, dict) else None,
                        "duration_s": data.get("time", {}).get("duration_s") if isinstance(data, dict) else None,
                        "dt_s": data.get("time", {}).get("dt_s") if isinstance(data, dict) else None,
                        "system": data.get("system", {}).get("builder") if isinstance(data, dict) else None,
                        "controller": data.get("system", {}).get("controller") if isinstance(data, dict) else None,
                        "environment": data.get("system", {}).get("environment") if isinstance(data, dict) else None,
                    }
                )
        except Exception as exc:
            experiments.append(
                {
                    "name": path.stem,
                    "path": rel,
                    "error": str(exc),
                    "updated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                    "updated_ts": stat.st_mtime,
                }
            )
    archived_experiments: list[dict[str, Any]] = []
    for path in sorted(archive_dir.glob("*.json")) if archive_dir.exists() else []:
        rel = _relative(path, workspace)
        stat = path.stat()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "scenario" in data:
                plan = load_experiment_plan(path)
                asset_meta = _plan_asset_meta_from_mapping(path, data)
                archived_experiments.append(
                    {
                        "name": plan.name,
                        "path": rel,
                        "scenario": plan.scenario.metadata.name,
                        "sweeps": len(plan.sweeps),
                        "monte_carlo_samples": 0 if plan.monte_carlo is None else plan.monte_carlo.samples,
                        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                        "updated_ts": stat.st_mtime,
                        **asset_meta,
                    }
                )
        except Exception as exc:
            archived_experiments.append(
                {
                    "name": path.stem,
                    "path": rel,
                    "error": str(exc),
                    "updated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                    "updated_ts": stat.st_mtime,
                }
            )
    dashboards = []
    for path in sorted(results_dir.rglob("dashboard.html")) if results_dir.exists() else []:
        dashboards.append(_dashboard_listing(workspace, path))
    asset_summary = _summarize_experiment_assets(experiments, archived_experiments)
    return {
        "workspace": str(workspace),
        "satmodel_version": __version__,
        "scenarios": scenarios,
        "experiments": experiments,
        "archived_experiments": archived_experiments,
        "dashboards": dashboards,
        "asset_summary": asset_summary,
    }


def platform_ui_health(root: str | Path) -> dict[str, Any]:
    """Return a lightweight health payload for local UI startup checks."""

    workspace = Path(root).resolve()
    summary = discover_workspace(workspace)
    return {
        "status": "ok",
        "workspace": summary["workspace"],
        "satmodel_version": summary["satmodel_version"],
        "scenario_count": len(summary["scenarios"]),
        "experiment_count": len(summary["experiments"]),
        "archived_experiment_count": len(summary.get("archived_experiments", [])),
        "dashboard_count": len(summary["dashboards"]),
        "library_experiment_count": summary.get("asset_summary", {}).get("library_count", 0),
        "needs_consolidation_count": summary.get("asset_summary", {}).get("needs_consolidation_count", 0),
    }


def describe_workspace_scenario(root: str | Path, scenario_path: str) -> dict[str, Any]:
    """Return a concise description of a workspace scenario file."""

    workspace = Path(root).resolve()
    scenario_file = _safe_path(workspace, scenario_path)
    spec = load_scenario(scenario_file)
    return {
        "name": spec.metadata.name,
        "path": _relative(scenario_file, workspace),
        "description": spec.metadata.description,
        "duration_s": spec.time.duration_s,
        "dt_s": spec.time.dt_s,
        "seed": spec.time.seed,
        "system": spec.system.builder,
        "controller": spec.system.controller,
        "environment": spec.system.environment,
        "fault_count": len(spec.faults),
        "output_root": spec.outputs.root,
    }


def validate_workspace_scenario(root: str | Path, scenario_path: str) -> dict[str, Any]:
    """Validate and compile a workspace scenario file."""

    workspace = Path(root).resolve()
    scenario_file = _safe_path(workspace, scenario_path)
    spec = load_scenario(scenario_file)
    compiled = compile_scenario(spec)
    return {
        "valid": True,
        "name": spec.metadata.name,
        "path": _relative(scenario_file, workspace),
        "duration_s": compiled.config.duration,
        "dt_s": compiled.config.dt,
        "system": spec.system.builder,
        "controller": spec.system.controller,
        "environment": spec.system.environment,
    }


def validate_workspace_experiment(root: str | Path, plan_path: str) -> dict[str, Any]:
    """Validate one experiment plan from a workspace-relative path."""

    workspace = Path(root).resolve()
    plan_file = _safe_path(workspace, plan_path)
    runner = ExperimentRunner(plan_file)
    cases = runner.validate()
    plan = runner.plan
    return {
        "valid": True,
        "name": plan.name,
        "path": _relative(plan_file, workspace),
        "runs": len(cases),
        "scenario": plan.scenario.metadata.name,
        "sweeps": len(plan.sweeps),
        "monte_carlo_samples": 0 if plan.monte_carlo is None else plan.monte_carlo.samples,
    }


def describe_workspace_experiment(root: str | Path, plan_path: str) -> dict[str, Any]:
    """Return the editable JSON source and summary for one experiment plan."""

    workspace = Path(root).resolve()
    plan_file = _safe_path(workspace, plan_path)
    text = plan_file.read_text(encoding="utf-8")
    mapping = json.loads(text)
    if not isinstance(mapping, dict):
        raise ValueError("experiment plan must be a mapping")
    validation = validate_workspace_experiment(workspace, _relative(plan_file, workspace))
    return {
        "path": _relative(plan_file, workspace),
        "name": validation["name"],
        "scenario": validation["scenario"],
        "runs": validation["runs"],
        "sweeps": validation["sweeps"],
        "monte_carlo_samples": validation["monte_carlo_samples"],
        "mapping": mapping,
        "text": text,
    }


def save_workspace_experiment(root: str | Path, plan_path: str, text: str) -> dict[str, Any]:
    """Validate and save one experiment plan edited in the local UI."""

    workspace = Path(root).resolve()
    plan_file = _safe_path(workspace, plan_path)
    mapping = json.loads(text)
    if not isinstance(mapping, dict):
        raise ValueError("experiment plan must be a mapping")
    experiment_plan_from_mapping(mapping, base_dir=plan_file.parent)
    formatted = json.dumps(mapping, indent=2, ensure_ascii=False) + "\n"
    plan_file.write_text(formatted, encoding="utf-8")
    validation = validate_workspace_experiment(workspace, _relative(plan_file, workspace))
    return {
        "path": _relative(plan_file, workspace),
        "name": validation["name"],
        "scenario": validation["scenario"],
        "runs": validation["runs"],
        "sweeps": validation["sweeps"],
        "monte_carlo_samples": validation["monte_carlo_samples"],
        "text": formatted,
        "mapping": mapping,
    }


def duplicate_workspace_experiment(
    root: str | Path,
    plan_path: str,
    new_name: str | None = None,
    output_root: str | None = None,
) -> dict[str, Any]:
    """Duplicate one experiment plan into a new plan file for quick iteration."""

    workspace = Path(root).resolve()
    source_file = _safe_path(workspace, plan_path)
    mapping = json.loads(source_file.read_text(encoding="utf-8"))
    if not isinstance(mapping, dict):
        raise ValueError("experiment plan must be a mapping")

    metadata_raw = mapping.get("metadata")
    metadata_map = metadata_raw if isinstance(metadata_raw, dict) else {}
    source_name = str(metadata_map.get("name") or source_file.stem)
    requested_name = str(new_name or f"{source_name}_copy").strip()
    target_file, name, requested_name, resolved_from_collision, slug = _allocate_plan_path(source_file.parent, requested_name)

    cloned = copy.deepcopy(mapping)
    metadata = dict(cloned.get("metadata") or {})
    metadata["name"] = name
    cloned["metadata"] = metadata

    outputs = dict(cloned.get("outputs") or {})
    requested_output = str(output_root or "").strip()
    outputs["root"] = requested_output or _default_output_root(slug)
    cloned["outputs"] = outputs

    experiment_plan_from_mapping(cloned, base_dir=target_file.parent)
    formatted = json.dumps(cloned, indent=2, ensure_ascii=False) + "\n"
    target_file.write_text(formatted, encoding="utf-8")
    validation = validate_workspace_experiment(workspace, _relative(target_file, workspace))
    return {
        "path": _relative(target_file, workspace),
        "name": name,
        "requested_name": requested_name,
        "resolved_from_collision": resolved_from_collision,
        "output_root": str(outputs["root"]),
        "validation": validation,
        "source_path": _relative(source_file, workspace),
        "text": formatted,
        "mapping": cloned,
    }


def rename_workspace_experiment(
    root: str | Path,
    plan_path: str,
    new_name: str,
    output_root: str | None = None,
) -> dict[str, Any]:
    """Rename one experiment plan and optionally realign its default output root."""

    workspace = Path(root).resolve()
    source_file = _safe_path(workspace, plan_path)
    mapping = json.loads(source_file.read_text(encoding="utf-8"))
    if not isinstance(mapping, dict):
        raise ValueError("experiment plan must be a mapping")

    metadata_raw = mapping.get("metadata")
    metadata_map = metadata_raw if isinstance(metadata_raw, dict) else {}
    current_name = str(metadata_map.get("name") or source_file.stem)
    target_file, name, requested_name, resolved_from_collision, slug = _allocate_plan_path(
        source_file.parent,
        new_name,
        ignore_path=source_file,
    )

    renamed = copy.deepcopy(mapping)
    metadata = dict(renamed.get("metadata") or {})
    metadata["name"] = name
    renamed["metadata"] = metadata

    outputs = dict(renamed.get("outputs") or {})
    old_default_root = _default_output_root(source_file.stem)
    requested_output = str(output_root or "").strip()
    if requested_output:
        outputs["root"] = requested_output
    elif str(outputs.get("root") or "").strip() in ("", old_default_root):
        outputs["root"] = _default_output_root(slug)
    renamed["outputs"] = outputs

    experiment_plan_from_mapping(renamed, base_dir=target_file.parent)
    formatted = json.dumps(renamed, indent=2, ensure_ascii=False) + "\n"
    target_file.write_text(formatted, encoding="utf-8")
    if target_file != source_file and source_file.exists():
        source_file.unlink()
    validation = validate_workspace_experiment(workspace, _relative(target_file, workspace))
    return {
        "path": _relative(target_file, workspace),
        "name": name,
        "previous_name": current_name,
        "requested_name": requested_name,
        "resolved_from_collision": resolved_from_collision,
        "output_root": str(outputs.get("root") or ""),
        "validation": validation,
        "source_path": _relative(source_file, workspace),
        "text": formatted,
        "mapping": renamed,
    }


def archive_workspace_experiment(root: str | Path, plan_path: str) -> dict[str, Any]:
    """Archive one experiment plan by moving it out of the active scenarios root."""

    workspace = Path(root).resolve()
    source_file = _safe_path(workspace, plan_path)
    mapping = json.loads(source_file.read_text(encoding="utf-8"))
    if not isinstance(mapping, dict):
        raise ValueError("experiment plan must be a mapping")

    metadata_raw = mapping.get("metadata")
    metadata_map = metadata_raw if isinstance(metadata_raw, dict) else {}
    name = str(metadata_map.get("name") or source_file.stem)

    archive_dir = source_file.parent / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_file = archive_dir / source_file.name
    if archive_file.exists():
        archive_slug, _ = _next_available_slug(archive_dir, source_file.stem)
        archive_file = archive_dir / f"{archive_slug}.json"

    archived = copy.deepcopy(mapping)
    _rebase_plan_scenario(archived, source_file.parent, archive_file.parent)
    archive_file.write_text(json.dumps(archived, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    source_file.unlink()
    return {
        "name": name,
        "source_path": _relative(source_file, workspace),
        "archived_path": _relative(archive_file, workspace),
    }


def restore_workspace_experiment(root: str | Path, archived_path: str) -> dict[str, Any]:
    """Restore one archived experiment plan back into the active scenarios root."""

    workspace = Path(root).resolve()
    source_file = _safe_path(workspace, archived_path)
    mapping = json.loads(source_file.read_text(encoding="utf-8"))
    if not isinstance(mapping, dict):
        raise ValueError("experiment plan must be a mapping")

    metadata_raw = mapping.get("metadata")
    metadata_map = metadata_raw if isinstance(metadata_raw, dict) else {}
    current_name = str(metadata_map.get("name") or source_file.stem)
    scenario_dir = workspace / "scenarios"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    target_file, name, requested_name, resolved_from_collision, slug = _allocate_plan_path(scenario_dir, current_name)

    restored = copy.deepcopy(mapping)
    _rebase_plan_scenario(restored, source_file.parent, target_file.parent)
    metadata = dict(restored.get("metadata") or {})
    metadata["name"] = name
    restored["metadata"] = metadata

    outputs = dict(restored.get("outputs") or {})
    old_default_root = _default_output_root(source_file.stem)
    if str(outputs.get("root") or "").strip() in ("", old_default_root):
        outputs["root"] = _default_output_root(slug)
    restored["outputs"] = outputs

    experiment_plan_from_mapping(restored, base_dir=target_file.parent)
    formatted = json.dumps(restored, indent=2, ensure_ascii=False) + "\n"
    target_file.write_text(formatted, encoding="utf-8")
    if source_file.exists():
        source_file.unlink()
    validation = validate_workspace_experiment(workspace, _relative(target_file, workspace))
    return {
        "path": _relative(target_file, workspace),
        "name": name,
        "requested_name": requested_name,
        "resolved_from_collision": resolved_from_collision,
        "output_root": str(outputs.get("root") or ""),
        "validation": validation,
        "source_path": _relative(source_file, workspace),
        "text": formatted,
        "mapping": restored,
    }


def describe_workspace_dashboard(root: str | Path, dashboard_path: str) -> dict[str, Any]:
    """Return a structured summary for one generated dashboard."""

    workspace = Path(root).resolve()
    dashboard_file = _safe_path(workspace, dashboard_path)
    if dashboard_file.name != "dashboard.html":
        raise ValueError(f"not a dashboard file: {dashboard_path}")
    return _dashboard_summary(workspace, dashboard_file)


def run_workspace_experiment(root: str | Path, plan_path: str, output_dir: str | None = None) -> dict[str, Any]:
    """Run one experiment plan from a workspace-relative path and return dashboard details."""

    workspace = Path(root).resolve()
    plan_file = _safe_path(workspace, plan_path)
    if output_dir:
        output = _safe_path(workspace, output_dir)
    else:
        output = workspace / "results" / "platform_ui" / f"{plan_file.stem}_{time.strftime('%Y%m%d_%H%M%S')}"
    summary = ExperimentRunner(plan_file, output_dir=output).run()
    dashboard = build_dashboard(summary.output_dir)
    acceptance = summary.acceptance_summary()
    best = summary.best_row()
    return {
        "output_dir": _relative(summary.output_dir, workspace),
        "dashboard": _relative(dashboard, workspace),
        "dashboard_url": _file_url(dashboard, workspace),
        "runs": len(summary.rows),
        "accepted": acceptance["accepted_count"],
        "failed": acceptance["failed_count"],
        "best_run": None if best is None else best.get("run_id"),
        "best_final_error_deg": None if best is None else best.get("final_error_deg"),
        "summary": _dashboard_summary(workspace, dashboard),
    }


def create_workspace_experiment_plan(root: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Create an experiment plan JSON file from local UI form values."""

    workspace = Path(root).resolve()
    scenario_path = _safe_path(workspace, str(payload["scenario_path"]))
    scenario_rel = _relative(scenario_path, scenario_path.parent)
    scenario_dir = workspace / "scenarios"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    target_file, name, requested_name, resolved_from_collision, slug = _allocate_plan_path(
        scenario_dir,
        str(payload.get("name") or scenario_path.stem),
        allow_existing=bool(payload.get("overwrite", False)),
    )

    requested_output_root = str(payload.get("output_root") or "").strip()
    default_output_root = _default_output_root(_slug(requested_name))
    output_root = requested_output_root
    if not output_root or output_root == default_output_root:
        output_root = _default_output_root(slug)
    plan: dict[str, Any] = {
        "schema_version": 1,
        "metadata": {
            "name": name,
            "description": str(payload.get("description") or f"Generated platform experiment for {scenario_path.stem}."),
            "tags": ["platform", "generated"],
        },
        "scenario": scenario_rel,
        "sweeps": [],
        "outputs": {
            "root": output_root,
        },
        "runtime": {"template": "single_rate"},
    }

    sweep_path = str(payload.get("sweep_path") or "").strip()
    sweep_values = _parse_list(payload.get("sweep_values"))
    if sweep_path and sweep_values:
        plan["sweeps"].append({"path": sweep_path, "values": sweep_values})
    second_sweep_path = str(payload.get("second_sweep_path") or "").strip()
    second_sweep_values = _parse_list(payload.get("second_sweep_values"))
    if second_sweep_path and second_sweep_values:
        if second_sweep_path == sweep_path:
            raise ValueError("second sweep path must differ from primary sweep path")
        plan["sweeps"].append({"path": second_sweep_path, "values": second_sweep_values})
    samples = int(payload.get("monte_carlo_samples") or 0)
    if samples > 0:
        monte_carlo: dict[str, Any] = {"samples": samples}
        if payload.get("monte_carlo_seed") not in (None, ""):
            monte_carlo["seed"] = int(payload["monte_carlo_seed"])
        plan["monte_carlo"] = monte_carlo

    mission_template = str(payload.get("mission_template") or "single_mode")
    if mission_template == "detumble_then_hold":
        plan["mission"] = {
            "template": "detumble_then_hold",
            "detumble_s": float(payload.get("detumble_s") or 0.5),
            "hold_mode": str(payload.get("hold_mode") or "inertial_hold"),
            "reference": str(payload.get("reference") or "body_zero"),
        }
    elif mission_template == "single_mode":
        plan["mission"] = {
            "template": "single_mode",
            "mode": str(payload.get("mode") or "inertial_hold"),
            "reference": str(payload.get("reference") or "body_zero"),
        }
    else:
        raise ValueError(f"unknown mission template: {mission_template}")

    acceptance: dict[str, Any] = {}
    if payload.get("acceptance_final_deg") not in (None, ""):
        acceptance["max_final_error_deg"] = float(payload["acceptance_final_deg"])
    if payload.get("acceptance_rms_deg") not in (None, ""):
        acceptance["max_rms_error_deg"] = float(payload["acceptance_rms_deg"])
    if payload.get("acceptance_peak_torque_nm") not in (None, ""):
        acceptance["max_peak_torque_nm"] = float(payload["acceptance_peak_torque_nm"])
    if acceptance:
        plan["acceptance"] = acceptance

    target_file.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    validation = validate_workspace_experiment(workspace, _relative(target_file, workspace))
    return {
        "path": _relative(target_file, workspace),
        "name": name,
        "requested_name": requested_name,
        "resolved_from_collision": resolved_from_collision,
        "output_root": str(plan["outputs"]["root"]),
        "validation": validation,
    }


def serve_platform_ui(
    root: str | Path = ".",
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
) -> ThreadingHTTPServer:
    """Serve the local platform UI until interrupted."""

    workspace = Path(root).resolve()

    class Handler(PlatformUIHandler):
        workspace_root = workspace

    server = ThreadingHTTPServer((host, int(port)), Handler)
    url = f"http://{host}:{server.server_address[1]}"
    if open_browser:
        webbrowser.open(url)
    print(f"satmodel platform UI: {url}")
    print(f"workspace: {workspace}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return server


class PlatformUIHandler(BaseHTTPRequestHandler):
    """HTTP handler for the local platform UI."""

    workspace_root = Path(".").resolve()

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_text(_render_home(), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/health":
            self._send_json(platform_ui_health(self.workspace_root))
            return
        if parsed.path == "/api/workspace":
            self._send_json(discover_workspace(self.workspace_root))
            return
        if parsed.path.startswith("/file/"):
            self._send_file(unquote(parsed.path.removeprefix("/file/")))
            return
        self._send_json({"error": "not found"}, status=404)

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        try:
            payload = self._read_json()
            if parsed.path == "/api/scenario":
                self._send_json(describe_workspace_scenario(self.workspace_root, payload["path"]))
                return
            if parsed.path == "/api/validate-scenario":
                self._send_json(validate_workspace_scenario(self.workspace_root, payload["path"]))
                return
            if parsed.path == "/api/validate-experiment":
                self._send_json(validate_workspace_experiment(self.workspace_root, payload["path"]))
                return
            if parsed.path == "/api/experiment":
                self._send_json(describe_workspace_experiment(self.workspace_root, payload["path"]))
                return
            if parsed.path == "/api/dashboard":
                self._send_json(describe_workspace_dashboard(self.workspace_root, payload["path"]))
                return
            if parsed.path == "/api/run-experiment":
                self._send_json(run_workspace_experiment(self.workspace_root, payload["path"], payload.get("output_dir")))
                return
            if parsed.path == "/api/save-experiment":
                self._send_json(save_workspace_experiment(self.workspace_root, payload["path"], payload["text"]))
                return
            if parsed.path == "/api/duplicate-experiment":
                self._send_json(
                    duplicate_workspace_experiment(
                        self.workspace_root,
                        payload["path"],
                        payload.get("name"),
                        payload.get("output_root"),
                    )
                )
                return
            if parsed.path == "/api/rename-experiment":
                self._send_json(
                    rename_workspace_experiment(
                        self.workspace_root,
                        payload["path"],
                        payload["name"],
                        payload.get("output_root"),
                    )
                )
                return
            if parsed.path == "/api/archive-experiment":
                self._send_json(archive_workspace_experiment(self.workspace_root, payload["path"]))
                return
            if parsed.path == "/api/restore-experiment":
                self._send_json(restore_workspace_experiment(self.workspace_root, payload["path"]))
                return
            if parsed.path == "/api/create-experiment":
                self._send_json(create_workspace_experiment_plan(self.workspace_root, payload))
                return
            self._send_json({"error": "not found"}, status=404)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=400)

    def log_message(self, format, *args):  # noqa: A002
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _send_json(self, payload: dict[str, Any], *, status: int = 200):
        self._send_bytes(
            json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
            "application/json; charset=utf-8",
            status=status,
        )

    def _send_text(self, text: str, content_type: str, *, status: int = 200):
        self._send_bytes(text.encode("utf-8"), content_type, status=status)

    def _send_file(self, relative_path: str):
        path = _safe_path(self.workspace_root, relative_path)
        if not path.exists() or not path.is_file():
            self._send_json({"error": "file not found"}, status=404)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._send_bytes(path.read_bytes(), content_type)

    def _send_bytes(self, data: bytes, content_type: str, *, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(data)


def _safe_path(root: Path, relative_path: str) -> Path:
    path = (root / relative_path).resolve()
    if path != root and root not in path.parents:
        raise ValueError(f"path escapes workspace: {relative_path}")
    return path


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _file_url(path: Path, root: Path) -> str:
    return f"/file/{_relative(path, root)}"


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip()).strip("._-")
    return slug or "experiment"


def _default_output_root(slug: str) -> str:
    return f"results/platform_ui/{slug}"


def _rebase_plan_scenario(mapping: dict[str, Any], source_parent: Path, target_parent: Path) -> None:
    scenario_value = mapping.get("scenario")
    if isinstance(scenario_value, str) and scenario_value.strip():
        source_scenario = (source_parent / scenario_value).resolve()
        mapping["scenario"] = Path(os.path.relpath(source_scenario, target_parent.resolve())).as_posix()


def _next_available_slug(directory: Path, base_slug: str) -> tuple[str, int]:
    suffix = 2
    while (directory / f"{base_slug}_{suffix}.json").exists():
        suffix += 1
    return f"{base_slug}_{suffix}", suffix


def _allocate_plan_path(
    directory: Path,
    requested_name: str,
    *,
    ignore_path: Path | None = None,
    allow_existing: bool = False,
) -> tuple[Path, str, str, bool, str]:
    requested_name = str(requested_name or "").strip()
    if not requested_name:
        raise ValueError("experiment name is required")
    slug = _slug(requested_name)
    plan_path = directory / f"{slug}.json"
    name = requested_name
    if allow_existing:
        return plan_path, name, requested_name, False, slug
    if plan_path.exists() and (ignore_path is None or plan_path.resolve() != ignore_path.resolve()):
        slug, suffix = _next_available_slug(directory, slug)
        name = f"{requested_name}_{suffix}"
        plan_path = directory / f"{slug}.json"
    return plan_path, name, requested_name, name != requested_name, slug


def _parse_list(value) -> list[Any]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return value
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
        return [parsed]
    except json.JSONDecodeError:
        values = []
        for item in text.split(","):
            item = item.strip()
            if not item:
                continue
            try:
                values.append(json.loads(item))
            except json.JSONDecodeError:
                values.append(item)
        return values


def _dashboard_listing(workspace: Path, dashboard_file: Path) -> dict[str, Any]:
    summary = _dashboard_summary(workspace, dashboard_file)
    stat = dashboard_file.stat()
    return {
        "name": summary["experiment_name"] or dashboard_file.parent.name,
        "path": summary["path"],
        "url": summary["url"],
        "scenario": summary["scenario_name"],
        "run_count": summary["run_count"],
        "accepted_count": summary["accepted_count"],
        "failed_count": summary["failed_count"],
        "acceptance_rate": summary["acceptance_rate"],
        "best_run_id": summary["best_run_id"],
        "best_final_error_deg": summary["best_final_error_deg"],
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
        "updated_ts": stat.st_mtime,
    }


def _dashboard_summary(workspace: Path, dashboard_file: Path) -> dict[str, Any]:
    experiment_dir = dashboard_file.parent
    index = _read_json_file(experiment_dir / "index.json")
    manifest = _read_json_file(experiment_dir / "experiment_manifest.json")
    timeline_name = index.get("mode_timeline")
    runtime_name = index.get("runtime_schedule")
    timeline_data = _read_json_file(experiment_dir / str(timeline_name)) if timeline_name else {}
    runtime_data = _read_json_file(experiment_dir / str(runtime_name)) if runtime_name else {}
    experiment = manifest.get("experiment", {})
    scenario = experiment.get("scenario", {})
    raw_runs = index.get("runs", []) if isinstance(index.get("runs"), list) else []
    runs = [_decorate_run_row(workspace, experiment_dir, row) for row in raw_runs if isinstance(row, dict)]
    best = _select_metric_row(runs, "final_error_deg", reverse=False)
    worst = _select_metric_row(runs, "final_error_deg", reverse=True)
    compare_run_ids = _compare_run_ids(runs, best, worst)
    files = []
    for name in [
        "README.md",
        "index.json",
        "summary_metrics.csv",
        "experiment_manifest.json",
        index.get("report_summary"),
        index.get("paper_summary"),
        index.get("runtime_schedule"),
        index.get("mode_timeline"),
        "dashboard.html",
    ]:
        if not name:
            continue
        path = experiment_dir / name
        if path.exists():
            files.append({"name": name, "url": _file_url(path, workspace)})
    return {
        "name": experiment_dir.name,
        "path": _relative(dashboard_file, workspace),
        "url": _file_url(dashboard_file, workspace),
        "output_dir": _relative(experiment_dir, workspace),
        "experiment_name": experiment.get("metadata", {}).get("name", experiment_dir.name),
        "scenario_name": scenario.get("metadata", {}).get("name"),
        "description": experiment.get("metadata", {}).get("description"),
        "run_count": int(index.get("run_count", len(runs))),
        "accepted_count": int(index.get("accepted_count", 0)),
        "failed_count": int(index.get("failed_count", 0)),
        "acceptance_rate": float(index.get("acceptance_rate", 0.0)),
        "best_run_id": index.get("best_run_id"),
        "best_output_dir": index.get("best_output_dir"),
        "best_final_error_deg": _metric_value(best, "final_error_deg"),
        "worst_run_id": None if worst is None else worst.get("run_id"),
        "worst_final_error_deg": _metric_value(worst, "final_error_deg"),
        "parameter_columns": list(index.get("parameter_columns", [])),
        "metric_columns": list(index.get("metric_columns", [])),
        "runs": runs,
        "best_run": best,
        "worst_run": worst,
        "compare_run_ids": compare_run_ids,
        "compare_histories": _compare_histories(experiment_dir, runs, compare_run_ids),
        "timeline": timeline_data,
        "runtime": _runtime_summary(runtime_data),
        "files": files,
        "readme_url": _file_url(experiment_dir / "README.md", workspace) if (experiment_dir / "README.md").exists() else None,
        "dashboard_url": _file_url(dashboard_file, workspace),
    }


def _decorate_run_row(workspace: Path, experiment_dir: Path, row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["artifacts"] = _run_artifacts(workspace, experiment_dir, item)
    history = _read_time_history(_time_history_path(experiment_dir, item))
    item["history_summary"] = _history_summary(history)
    return item


def _run_artifacts(workspace: Path, experiment_dir: Path, row: dict[str, Any]) -> dict[str, str]:
    run_dir = _run_directory(experiment_dir, row)
    artifacts: dict[str, str] = {}
    for name in ["README.md", "manifest.json", "metrics.csv", "time_history.csv", "events.csv"]:
        path = run_dir / name
        if path.exists():
            artifacts[name] = _file_url(path, workspace)
    return artifacts


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _select_metric_row(rows: list[dict[str, Any]], metric: str, *, reverse: bool) -> dict[str, Any] | None:
    candidates = [row for row in rows if _metric_value(row, metric) is not None]
    if not candidates:
        return rows[0] if rows else None
    return sorted(candidates, key=lambda row: _metric_value(row, metric), reverse=reverse)[0]


def _metric_value(row: dict[str, Any] | None, metric: str) -> float | None:
    if row is None:
        return None
    value = row.get(metric)
    try:
        return None if value is None or value == "" else float(value)
    except (TypeError, ValueError):
        return None


def _compare_run_ids(
    runs: list[dict[str, Any]],
    best: dict[str, Any] | None,
    worst: dict[str, Any] | None,
    *,
    limit: int = 4,
) -> list[str]:
    ordered: list[str] = []
    for row in [best, worst, *runs]:
        run_id = None if row is None else row.get("run_id")
        if not run_id or run_id in ordered:
            continue
        ordered.append(run_id)
        if len(ordered) >= limit:
            break
    return ordered


def _compare_histories(
    experiment_dir: Path,
    runs: list[dict[str, Any]],
    run_ids: list[str],
) -> dict[str, list[dict[str, Any]]]:
    run_map = {str(row.get("run_id")): row for row in runs if row.get("run_id")}
    histories: dict[str, list[dict[str, Any]]] = {}
    for run_id in run_ids:
        row = run_map.get(run_id)
        if row is None:
            continue
        path = _time_history_path(experiment_dir, row)
        rows = _read_time_history(path)
        if rows:
            histories[run_id] = _sample_history(rows, max_points=260)
    return histories


def _time_history_path(experiment_dir: Path, row: dict[str, Any]) -> Path:
    run_dir = _run_directory(experiment_dir, row)
    path = run_dir / "time_history.csv"
    if path.exists():
        return path
    return path


def _run_directory(experiment_dir: Path, row: dict[str, Any]) -> Path:
    output_dir = row.get("output_dir")
    run_id = row.get("run_id")
    candidates: list[Path] = []
    if output_dir:
        run_dir = Path(str(output_dir))
        candidates.extend([run_dir, experiment_dir / run_dir, experiment_dir / run_dir.name])
    if run_id:
        candidates.append(experiment_dir / str(run_id))
    if not candidates:
        candidates.append(experiment_dir)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _read_time_history(path: Path) -> list[dict[str, Any]]:
    base_columns = {
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
    }
    rows = _read_csv_file(path)
    result: list[dict[str, Any]] = []
    dynamic_columns = {
        key
        for row in rows
        for key in row
        if key.endswith("_torque_norm_nm")
    }
    columns = base_columns | dynamic_columns
    for row in rows:
        item: dict[str, Any] = {}
        for key in columns:
            value = row.get(key)
            if value in (None, ""):
                continue
            try:
                item[key] = float(value)
            except (TypeError, ValueError):
                item[key] = value
        if item:
            result.append(item)
    return result


def _history_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}

    def _peak(keys: tuple[str, str, str]) -> float | None:
        values: list[float] = []
        for row in rows:
            axis = [row.get(name) for name in keys]
            if any(value is None for value in axis):
                continue
            try:
                x, y, z = (float(axis[0]), float(axis[1]), float(axis[2]))
            except (TypeError, ValueError):
                continue
            values.append((x * x + y * y + z * z) ** 0.5)
        return 0.0 if not values else max(values)

    disturbance_peaks: dict[str, float] = {}
    for key in rows[0]:
        if not str(key).endswith("_torque_norm_nm"):
            continue
        values = []
        for row in rows:
            value = row.get(key)
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            values.append(number)
        if values:
            disturbance_peaks[str(key)] = max(values)

    dominant_term = None
    dominant_peak = None
    if disturbance_peaks:
        dominant_term, dominant_peak = max(disturbance_peaks.items(), key=lambda item: item[1])

    return {
        "samples": len(rows),
        "duration_s": rows[-1].get("time_s"),
        "final_attitude_error_deg": rows[-1].get("attitude_error_deg"),
        "peak_omega_rad_s": _peak(("omega_x_rad_s", "omega_y_rad_s", "omega_z_rad_s")),
        "peak_applied_torque_nm": _peak(("applied_torque_x_nm", "applied_torque_y_nm", "applied_torque_z_nm")),
        "peak_disturbance_torque_nm": _peak(("disturbance_torque_x_nm", "disturbance_torque_y_nm", "disturbance_torque_z_nm")),
        "disturbance_term_peaks": disturbance_peaks,
        "dominant_disturbance_term": dominant_term,
        "dominant_disturbance_peak_nm": dominant_peak,
    }


def _read_csv_file(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _sample_history(rows: list[dict[str, Any]], *, max_points: int) -> list[dict[str, Any]]:
    if len(rows) <= max_points:
        return rows
    step = max(1, len(rows) // max_points)
    sampled = rows[::step]
    if sampled[-1] != rows[-1]:
        sampled.append(rows[-1])
    return sampled


def _runtime_summary(runtime_data: dict[str, Any]) -> dict[str, Any]:
    events = runtime_data.get("events", []) if isinstance(runtime_data.get("events"), list) else []
    if not events:
        return {}
    snapshots: list[dict[str, Any]] = []
    current_time = None
    current_events: list[dict[str, Any]] = []
    for event in events:
        time_s = float(event.get("time_s", 0.0))
        if current_time is None or abs(time_s - current_time) < 1e-12:
            current_time = time_s
            current_events.append(event)
            continue
        snapshots.append(_runtime_snapshot(current_time, current_events))
        current_time = time_s
        current_events = [event]
    if current_events:
        snapshots.append(_runtime_snapshot(float(current_time or 0.0), current_events))
    return {
        "name": runtime_data.get("runtime", {}).get("name"),
        "duration_s": runtime_data.get("duration_s"),
        "event_count": runtime_data.get("event_count"),
        "snapshots": snapshots[:220],
    }


def _runtime_snapshot(time_s: float, events: list[dict[str, Any]]) -> dict[str, Any]:
    first = events[0] if events else {}
    modules = [str(item.get("module")) for item in events if item.get("module")]
    roles = [str(item.get("role")) for item in events if item.get("role")]
    return {
        "time_s": time_s,
        "task": first.get("task"),
        "process": first.get("process"),
        "modules": modules,
        "roles": roles,
    }


def _render_home() -> str:
    build_stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(Path(__file__).stat().st_mtime))
    html = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>satmodel 仿真平台</title>
  <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%23172126'/%3E%3Cpath d='M14 32c8-11 28-18 36-10-9-2-22 2-29 10 11-4 20-2 29 6-12-4-24-2-36 10 8-12 8-22 0-16z' fill='%23f0cb61'/%3E%3C/svg%3E">
  <style>
    :root {
      --bg: #eef2f5;
      --panel: rgba(255, 255, 255, 0.96);
      --ink: #17202a;
      --muted: #5f6e82;
      --line: #d5dce7;
      --accent: #0f6c7b;
      --accent-strong: #124e78;
      --warm: #b96a10;
      --ok: #247a48;
      --bad: #b42318;
      --shadow: 0 16px 40px rgba(23, 32, 42, 0.08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      font: 14px/1.5 "Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif;
      letter-spacing: 0;
      background:
        linear-gradient(180deg, rgba(15, 108, 123, 0.08), rgba(15, 108, 123, 0) 220px),
        repeating-linear-gradient(0deg, rgba(18, 78, 120, 0.035), rgba(18, 78, 120, 0.035) 1px, transparent 1px, transparent 34px),
        repeating-linear-gradient(90deg, rgba(18, 78, 120, 0.03), rgba(18, 78, 120, 0.03) 1px, transparent 1px, transparent 34px),
        var(--bg);
    }
    .app-shell {
      min-height: 100vh;
    }
    .sidebar {
      position: fixed;
      inset: 0 auto 0 0;
      width: 280px;
      height: 100vh;
      overflow-y: auto;
      isolation: isolate;
      z-index: 20;
      pointer-events: auto;
      box-sizing: border-box;
      padding: 22px 18px;
      background: #172126;
      color: #eef4f8;
      border-right: 1px solid rgba(255, 255, 255, 0.06);
      display: flex;
      flex-direction: column;
      gap: 18px;
    }
    .sidebar-brand {
      display: grid;
      grid-template-columns: 56px minmax(0, 1fr);
      gap: 12px;
      align-items: center;
    }
    .sidebar-badge {
      width: 56px;
      height: 56px;
      border-radius: 14px;
      display: grid;
      place-items: center;
      background: linear-gradient(180deg, #f0cb61, #d6a840);
      color: #172126;
      font-size: 13px;
      font-weight: 800;
    }
    .sidebar-brand strong {
      display: block;
      font-size: 16px;
    }
    .sidebar-brand span {
      display: block;
      margin-top: 4px;
      color: rgba(238, 244, 248, 0.68);
      font-size: 12px;
      line-height: 1.45;
    }
    .sidebar-section-label {
      color: rgba(238, 244, 248, 0.55);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin: 6px 0 0;
    }
    .sidebar-nav {
      display: grid;
      gap: 8px;
      position: relative;
      z-index: 2;
    }
    .sidebar,
    .sidebar * {
      pointer-events: auto;
    }
    .sidebar-nav a,
    .sidebar-nav button {
      position: relative;
      z-index: 3;
      pointer-events: auto;
      display: block;
      text-decoration: none;
      width: 100%;
      justify-content: flex-start;
      text-align: left;
      min-height: 46px;
      cursor: pointer;
      touch-action: manipulation;
      user-select: none;
      -webkit-user-select: none;
      border-radius: 12px;
      border: 1px solid rgba(255, 255, 255, 0.08);
      background: transparent;
      color: rgba(238, 244, 248, 0.86);
      padding: 10px 14px;
      font-size: 14px;
      font-weight: 650;
    }
    .sidebar-nav a span,
    .sidebar-nav button span {
      display: block;
      margin-top: 4px;
      color: rgba(238, 244, 248, 0.58);
      font-size: 11px;
      font-weight: 500;
      line-height: 1.35;
    }
    .sidebar-nav a.active,
    .sidebar-nav button.active {
      background: rgba(255, 255, 255, 0.1);
      border-color: rgba(255, 255, 255, 0.12);
      color: #fff;
      box-shadow: inset 0 0 0 1px rgba(255,255,255,0.03);
    }
    .sidebar-nav a.active span,
    .sidebar-nav button.active span {
      color: rgba(255, 255, 255, 0.72);
    }
    .sidebar-meta {
      margin-top: auto;
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 12px;
      padding: 12px 14px;
      background: rgba(255, 255, 255, 0.04);
    }
    .sidebar-meta strong {
      display: block;
      font-size: 12px;
      color: rgba(255, 255, 255, 0.82);
    }
    .sidebar-meta span {
      display: block;
      margin-top: 4px;
      font-size: 11px;
      line-height: 1.45;
      color: rgba(238, 244, 248, 0.6);
      word-break: break-word;
    }
    .content-shell {
      margin-left: 280px;
      min-height: 100vh;
      min-width: 0;
      position: relative;
      z-index: 1;
    }
    header {
      padding: 24px 24px 12px;
    }
    .hero {
      max-width: 1450px;
      margin: 0 auto;
      border: 1px solid rgba(18, 78, 120, 0.16);
      border-radius: 18px;
      padding: 24px;
      background:
        linear-gradient(135deg, rgba(18, 78, 120, 0.96), rgba(15, 108, 123, 0.86) 58%, rgba(185, 106, 16, 0.85));
      color: #fff;
      box-shadow: var(--shadow);
      display: grid;
      grid-template-columns: minmax(0, 1.2fr) minmax(300px, 0.8fr);
      gap: 16px;
      align-items: end;
    }
    .eyebrow {
      margin: 0 0 8px;
      font-size: 12px;
      text-transform: uppercase;
      opacity: 0.85;
    }
    h1 {
      margin: 0;
      font: 700 30px/1.15 "Georgia", "Noto Serif SC", "STSong", serif;
    }
    .lead {
      margin: 10px 0 0;
      max-width: 760px;
      color: rgba(255, 255, 255, 0.88);
      font-size: 15px;
    }
    .hero-panel {
      border: 1px solid rgba(255, 255, 255, 0.2);
      border-radius: 14px;
      padding: 14px 16px;
      background: rgba(255, 255, 255, 0.12);
      backdrop-filter: blur(6px);
    }
    .hero-panel span {
      display: block;
      font-size: 12px;
      opacity: 0.82;
    }
    .hero-panel strong {
      display: block;
      margin-top: 6px;
      font-size: 16px;
      word-break: break-all;
    }
    main {
      max-width: 1450px;
      margin: 0 auto;
      padding: 16px 24px 36px;
    }
    .page-nav {
      display: none;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
      margin-bottom: 12px;
    }
    .page-nav a,
    .page-nav button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 126px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 16px;
      background: rgba(255, 255, 255, 0.94);
      color: var(--ink);
      text-decoration: none;
      font-weight: 650;
      cursor: pointer;
    }
    .page-nav a.active,
    .page-nav button.active {
      background: var(--accent-strong);
      border-color: var(--accent-strong);
      color: #fff;
      box-shadow: 0 12px 24px rgba(18, 78, 120, 0.16);
    }
    .page-summary {
      margin-bottom: 16px;
      padding: 12px 14px;
      border: 1px solid rgba(18, 78, 120, 0.14);
      border-radius: 12px;
      background: rgba(255, 255, 255, 0.84);
      color: var(--muted);
    }
    .page-summary strong {
      color: var(--ink);
    }
    .page-view[hidden] {
      display: none !important;
    }
    .page-view + .page-view {
      margin-top: 0;
    }
    .workspace-shell {
      display: grid;
      gap: 16px;
    }
    .workspace-layout {
      display: grid;
      grid-template-columns: 236px minmax(0, 1fr);
      gap: 16px;
      align-items: start;
    }
    .workspace-sidecard {
      position: sticky;
      top: 16px;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: var(--panel);
      padding: 14px;
      box-shadow: var(--shadow);
    }
    .workspace-sidecard strong {
      display: block;
      font-size: 15px;
    }
    .workspace-sidecard p {
      margin: 8px 0 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.55;
    }
    .workspace-content {
      min-width: 0;
    }
    .workspace-nav {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      align-items: center;
    }
    .workspace-nav.workspace-nav-vertical {
      display: grid;
      gap: 8px;
      margin-top: 12px;
    }
    .workspace-nav.workspace-nav-vertical button {
      width: 100%;
      min-width: 0;
      min-height: 52px;
      justify-content: flex-start;
      text-align: left;
      padding: 10px 12px;
    }
    .workspace-nav.workspace-nav-vertical button span {
      display: block;
      margin-top: 4px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 500;
      line-height: 1.4;
    }
    .workspace-nav.workspace-nav-vertical button.active span {
      color: rgba(255, 255, 255, 0.82);
    }
    .workspace-nav button {
      min-width: 126px;
      padding: 9px 14px;
      background: rgba(255, 255, 255, 0.94);
    }
    .workspace-nav button.active {
      background: var(--accent-strong);
      border-color: var(--accent-strong);
      color: #fff;
      box-shadow: 0 10px 20px rgba(18, 78, 120, 0.14);
    }
    .workspace-summary {
      padding: 12px 14px;
      border: 1px solid rgba(18, 78, 120, 0.14);
      border-radius: 12px;
      background: rgba(255, 255, 255, 0.84);
      color: var(--muted);
    }
    .workspace-summary strong {
      color: var(--ink);
    }
    .workspace-nav-note {
      margin-top: 12px;
      padding: 10px 12px;
      border: 1px solid rgba(18, 78, 120, 0.12);
      border-radius: 12px;
      background: #fbfcfe;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.55;
    }
    .workspace-view[hidden] {
      display: none !important;
    }
    .workspace-view-stack {
      display: grid;
      gap: 16px;
    }
    .section-stack {
      display: grid;
      gap: 16px;
    }
    .workbench-hero-grid {
      display: grid;
      grid-template-columns: minmax(0, 1.08fr) minmax(340px, 0.92fr);
      gap: 16px;
      align-items: start;
    }
    .compact-section {
      margin-bottom: 0;
    }
    .single-column-grid {
      display: grid;
      gap: 16px;
    }
    .grid {
      display: grid;
      grid-template-columns: minmax(0, 1.12fr) minmax(380px, 0.88fr);
      gap: 16px;
      align-items: start;
    }
    .grid + .grid,
    .grid + section {
      margin-top: 16px;
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 16px;
      margin-bottom: 16px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .intro-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }
    .process-strip {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 10px;
      margin-top: 12px;
    }
    .process-step {
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 10px 12px;
      background: #fbfcfe;
    }
    .process-step-action {
      width: 100%;
      min-height: 0;
      border: 0;
      padding: 0;
      background: transparent;
      text-align: left;
      color: inherit;
      cursor: pointer;
    }
    .process-step span {
      display: block;
      color: var(--muted);
      font-size: 12px;
    }
    .process-step strong {
      display: block;
      margin-top: 4px;
      font-size: 14px;
    }
    .process-step p {
      margin: 6px 0 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
    .diagram-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      margin-top: 12px;
    }
    .diagram-card {
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 14px;
      background: linear-gradient(180deg, #ffffff, #f7fafc);
    }
    .diagram-card strong {
      display: block;
      font-size: 14px;
    }
    .diagram-card p {
      margin: 6px 0 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }
    .flow-sequence {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 10px;
      margin-top: 12px;
    }
    .flow-node {
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px;
      background: linear-gradient(180deg, #ffffff, #f7fafc);
      min-height: 110px;
      position: relative;
      overflow: hidden;
    }
    .flow-node::before {
      content: "";
      position: absolute;
      left: 0;
      top: 0;
      bottom: 0;
      width: 4px;
      background: rgba(18, 78, 120, 0.24);
    }
    .flow-node.accent::before {
      background: rgba(18, 78, 120, 0.82);
    }
    .flow-node.ok::before {
      background: rgba(36, 122, 72, 0.78);
    }
    .flow-node.warm::before {
      background: rgba(185, 106, 16, 0.78);
    }
    .flow-node small {
      display: block;
      color: var(--muted);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .flow-node strong {
      display: block;
      margin-top: 6px;
      font-size: 15px;
    }
    .flow-node p {
      margin: 8px 0 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }
    .overview-entry-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      margin-top: 10px;
    }
    .overview-entry-card {
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 14px;
      background: linear-gradient(180deg, #ffffff, #f7fafc);
      display: grid;
      gap: 8px;
      min-height: 240px;
    }
    .overview-entry-card > span {
      display: block;
      color: var(--muted);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .overview-entry-card > strong {
      display: block;
      font-size: 16px;
      line-height: 1.35;
    }
    .overview-entry-card > p {
      margin: 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.55;
    }
    .ribbon {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 10px;
    }
    .ribbon-step {
      border: 1px solid rgba(18, 78, 120, 0.14);
      border-radius: 999px;
      padding: 7px 12px;
      background: #fbfcfe;
      color: var(--ink);
      font-size: 12px;
      font-weight: 600;
    }
    h2 {
      margin: 0 0 12px;
      font-size: 16px;
      font-weight: 700;
    }
    table { width: 100%; border-collapse: collapse; table-layout: fixed; }
    th, td {
      border-bottom: 1px solid #e8ecf2;
      padding: 8px 7px;
      text-align: left;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    th {
      color: var(--muted);
      background: #f9fbfc;
      font-weight: 650;
    }
    button, input, select {
      min-height: 36px;
      border-radius: 8px;
      border: 1px solid var(--line);
      padding: 8px 10px;
      background: #fff;
      color: var(--ink);
    }
    button {
      cursor: pointer;
      font-weight: 650;
    }
    button.primary {
      background: var(--accent-strong);
      border-color: var(--accent-strong);
      color: white;
    }
    button.secondary {
      background: #f8fafc;
      border-color: #c7d2df;
    }
    label {
      display: grid;
      gap: 5px;
      color: var(--muted);
      font-size: 12px;
    }
    label input, label select { font-size: 14px; }
    .toolbar {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      align-items: center;
      margin-bottom: 10px;
    }
    .form-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }
    .builder-textarea {
      width: 100%;
      min-height: 88px;
      resize: vertical;
      border-radius: 10px;
      border: 1px solid var(--line);
      padding: 10px 12px;
      background: #fff;
      color: var(--ink);
      font: 14px/1.5 "Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif;
    }
    .segment-control {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 4px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: #fbfcfe;
    }
    .segment-control button {
      min-height: 34px;
      border-radius: 999px;
      border: 0;
      background: transparent;
      color: var(--muted);
      padding: 6px 14px;
    }
    .segment-control button.active {
      background: var(--accent);
      color: #fff;
      box-shadow: 0 6px 12px rgba(18, 78, 120, 0.16);
    }
    .builder-mode-shell {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
      margin: 12px 0 6px;
    }
    .builder-mode-note {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
    .editor-view-shell {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
      margin: 0 0 10px;
    }
    .editor-view-note {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
    .full { grid-column: 1 / -1; }
    .path, .subtle {
      color: var(--muted);
      font-size: 13px;
    }
    .callout {
      border: 1px solid #d6e2ea;
      border-radius: 12px;
      padding: 12px 14px;
      background: linear-gradient(180deg, #fbfdff, #f4f8fb);
      margin-bottom: 12px;
    }
    .callout strong {
      display: block;
      margin-bottom: 5px;
      font-size: 13px;
    }
    .callout p {
      margin: 0;
      color: var(--muted);
      font-size: 12px;
    }
    .callout.success {
      border-color: rgba(36, 122, 72, 0.24);
      background: linear-gradient(180deg, rgba(36, 122, 72, 0.08), rgba(36, 122, 72, 0.03));
    }
    .callout.warning {
      border-color: rgba(185, 106, 16, 0.28);
      background: linear-gradient(180deg, rgba(185, 106, 16, 0.09), rgba(185, 106, 16, 0.03));
    }
    .callout.info {
      border-color: rgba(18, 78, 120, 0.24);
      background: linear-gradient(180deg, rgba(18, 78, 120, 0.08), rgba(18, 78, 120, 0.03));
    }
    .callout.danger {
      border-color: rgba(171, 45, 45, 0.28);
      background: linear-gradient(180deg, rgba(171, 45, 45, 0.08), rgba(171, 45, 45, 0.03));
    }
    .callout strong.inline-title {
      display: inline;
      margin: 0;
    }
    .callout.busy {
      position: relative;
      overflow: hidden;
    }
    .callout.busy::after {
      content: "";
      position: absolute;
      left: -30%;
      top: 0;
      bottom: 0;
      width: 30%;
      background: linear-gradient(90deg, rgba(255,255,255,0), rgba(255,255,255,0.7), rgba(255,255,255,0));
      animation: busy-sheen 1.2s linear infinite;
    }
    @keyframes busy-sheen {
      from { transform: translateX(0); }
      to { transform: translateX(450%); }
    }
    .template-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin: 12px 0;
    }
    .template-card {
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px;
      background: #fbfcfe;
      cursor: pointer;
      transition: border-color 120ms ease, box-shadow 120ms ease, transform 120ms ease;
    }
    .template-card:hover {
      border-color: rgba(18, 78, 120, 0.35);
      box-shadow: 0 10px 18px rgba(18, 78, 120, 0.08);
      transform: translateY(-1px);
    }
    .template-card.active {
      border-color: var(--accent-strong);
      background: linear-gradient(180deg, rgba(18, 78, 120, 0.08), rgba(18, 78, 120, 0.02));
      box-shadow: 0 10px 18px rgba(18, 78, 120, 0.1);
    }
    .template-card span {
      display: block;
      color: var(--muted);
      font-size: 12px;
    }
    .template-card strong {
      display: block;
      margin-top: 4px;
      font-size: 14px;
    }
    .template-card p {
      margin: 8px 0 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
    .history-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 12px;
    }
    .experiment-blueprint-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .experiment-blueprint {
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px;
      background: linear-gradient(180deg, #ffffff, #f8fbfc);
    }
    .experiment-blueprint span {
      display: block;
      color: var(--muted);
      font-size: 12px;
    }
    .experiment-blueprint strong {
      display: block;
      margin-top: 4px;
      font-size: 14px;
    }
    .experiment-blueprint p {
      margin: 8px 0 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }
    .history-card {
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px;
      background: #fbfcfe;
    }
    .history-card.active {
      border-color: var(--accent-strong);
      background: linear-gradient(180deg, rgba(18, 78, 120, 0.08), rgba(18, 78, 120, 0.02));
    }
    .history-card span {
      display: block;
      color: var(--muted);
      font-size: 12px;
    }
    .history-card strong {
      display: block;
      margin-top: 4px;
      font-size: 14px;
    }
    .history-card p {
      margin: 8px 0 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
    .picker-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }
    .picker-card {
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px;
      background: linear-gradient(180deg, #ffffff, #f8fbfc);
    }
    .picker-card.active {
      border-color: var(--accent-strong);
      background: linear-gradient(180deg, rgba(18, 78, 120, 0.08), rgba(18, 78, 120, 0.02));
      box-shadow: 0 10px 18px rgba(18, 78, 120, 0.08);
    }
    .picker-card span {
      display: block;
      color: var(--muted);
      font-size: 12px;
    }
    .picker-card strong {
      display: block;
      margin-top: 4px;
      font-size: 15px;
    }
    .picker-card p {
      margin: 8px 0 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
    .quick-select-panel {
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px;
      background: #fbfcfe;
      margin: 10px 0 12px;
    }
    .quick-select-panel strong {
      display: block;
      font-size: 14px;
      margin-bottom: 4px;
    }
    .quick-select-panel p {
      margin: 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }
    .quick-select-grid {
      display: grid;
      grid-template-columns: minmax(0, 1.4fr) repeat(3, auto);
      gap: 10px;
      margin-top: 10px;
      align-items: center;
    }
    .quick-select-grid select {
      width: 100%;
    }
    .quick-select-note {
      margin-top: 10px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }
    .selector-summary {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
      margin-bottom: 8px;
    }
    .selector-summary strong {
      margin: 0;
    }
    .selector-summary span {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
    .library-shell {
      display: grid;
      grid-template-columns: minmax(280px, 0.88fr) minmax(0, 1.12fr);
      gap: 16px;
      align-items: start;
    }
    .library-panel {
      border: 1px solid var(--line);
      border-radius: 14px;
      background: #fbfcfe;
      padding: 14px;
    }
    .library-panel h3 {
      margin: 0 0 10px;
      font-size: 14px;
    }
    .library-caption {
      margin: 0 0 10px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }
    .library-category-list,
    .library-experiment-list {
      display: grid;
      gap: 8px;
    }
    .library-category-button,
    .library-experiment-button {
      width: 100%;
      justify-content: flex-start;
      text-align: left;
      border-radius: 12px;
      padding: 10px 12px;
      background: #fff;
    }
    .library-category-button.active,
    .library-experiment-button.active {
      background: rgba(18, 78, 120, 0.1);
      border-color: rgba(18, 78, 120, 0.28);
      color: var(--ink);
      box-shadow: 0 8px 18px rgba(18, 78, 120, 0.08);
    }
    .library-category-button strong,
    .library-experiment-button strong {
      display: block;
      font-size: 14px;
    }
    .library-category-button span,
    .library-experiment-button span {
      display: block;
      margin-top: 4px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
    .library-section-divider {
      height: 1px;
      background: var(--line);
      margin: 12px 0;
    }
    .library-detail-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      margin-top: 12px;
    }
    .library-detail-grid .detail-box {
      min-height: 108px;
    }
    .library-detail-lead {
      margin: 0;
      color: var(--muted);
      line-height: 1.6;
    }
    .library-detail-actions {
      margin-top: 12px;
    }
    .result-summary-shell {
      margin-top: 10px;
    }
    .result-summary-head {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
      margin: 10px 0;
    }
    .result-summary-note {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }
    .result-summary-panel[hidden] {
      display: none !important;
    }
    .figure-guide-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin-top: 10px;
    }
    .figure-guide-card {
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px;
      background: #fbfcfe;
    }
    .figure-guide-card span {
      display: block;
      color: var(--muted);
      font-size: 12px;
    }
    .figure-guide-card strong {
      display: block;
      margin-top: 4px;
      font-size: 14px;
    }
    .figure-guide-card p {
      margin: 8px 0 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }
    .roadmap-stack {
      display: grid;
      gap: 10px;
      margin-top: 10px;
    }
    .roadmap-step {
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px;
      background: #fbfcfe;
    }
    .roadmap-step.current {
      border-color: rgba(18, 78, 120, 0.28);
      background: linear-gradient(180deg, rgba(18, 78, 120, 0.1), rgba(18, 78, 120, 0.03));
      box-shadow: 0 10px 18px rgba(18, 78, 120, 0.08);
    }
    .roadmap-step.upcoming {
      border-style: dashed;
    }
    .roadmap-step span {
      display: block;
      color: var(--muted);
      font-size: 12px;
    }
    .roadmap-step strong {
      display: block;
      margin-top: 4px;
      font-size: 14px;
    }
    .roadmap-step p {
      margin: 8px 0 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }
    .builder-category-shell {
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px;
      background: #fbfcfe;
      margin-top: 10px;
    }
    .builder-category-copy {
      margin-top: 8px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }
    .history-timeline {
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 10px;
      background: #fbfcfe;
      margin-bottom: 12px;
    }
    .history-timeline h3 {
      margin: 0 0 10px;
      font-size: 13px;
    }
    .history-items {
      display: grid;
      gap: 8px;
    }
    .history-item {
      display: grid;
      grid-template-columns: 104px minmax(0, 1fr) auto;
      gap: 10px;
      align-items: start;
      width: 100%;
      border: 1px solid #e4eaf2;
      border-radius: 10px;
      padding: 10px;
      background: #fff;
      text-align: left;
      cursor: pointer;
    }
    .history-item.active {
      border-color: var(--accent-strong);
      background: linear-gradient(180deg, rgba(18, 78, 120, 0.08), rgba(18, 78, 120, 0.02));
    }
    .history-time {
      color: var(--muted);
      font-size: 12px;
    }
    .history-item strong {
      display: block;
      font-size: 13px;
    }
    .history-item p {
      margin: 4px 0 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
    .activity-feed {
      display: grid;
      gap: 8px;
    }
    .activity-item {
      border: 1px solid #e4eaf2;
      border-radius: 10px;
      padding: 10px 12px;
      background: #fbfcfe;
    }
    .activity-item span {
      display: block;
      color: var(--muted);
      font-size: 12px;
    }
    .activity-item strong {
      display: block;
      margin-top: 3px;
      font-size: 13px;
    }
    .activity-item p {
      margin: 6px 0 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
    .history-detail {
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px;
      background: #fbfcfe;
      margin-bottom: 12px;
    }
    .history-detail strong {
      display: block;
      font-size: 14px;
    }
    .history-detail p {
      margin: 8px 0 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }
    .result-banner {
      border: 1px solid rgba(18, 78, 120, 0.22);
      border-radius: 12px;
      padding: 12px;
      margin-bottom: 12px;
      background: linear-gradient(135deg, rgba(18, 78, 120, 0.1), rgba(15, 108, 123, 0.05));
    }
    .result-banner span {
      display: block;
      color: var(--muted);
      font-size: 12px;
    }
    .result-banner strong {
      display: block;
      margin-top: 4px;
      font-size: 15px;
    }
    .run-status-stack {
      display: grid;
      gap: 10px;
    }
    .progress-track {
      position: relative;
      height: 10px;
      border-radius: 999px;
      background: #dce7ef;
      overflow: hidden;
      margin: 12px 0 8px;
    }
    .progress-fill {
      height: 100%;
      border-radius: 999px;
      background: linear-gradient(90deg, #124e78, #2b88a5);
      transition: width 180ms ease;
    }
    .progress-meta {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 10px;
      flex-wrap: wrap;
    }
    .stage-list {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 8px;
    }
    .stage-chip {
      display: inline-flex;
      align-items: center;
      min-height: 28px;
      padding: 4px 10px;
      border-radius: 999px;
      border: 1px solid #d4dce8;
      background: #f9fbfc;
      color: var(--muted);
      font-size: 12px;
    }
    .stage-chip.active {
      border-color: rgba(18, 78, 120, 0.28);
      background: rgba(18, 78, 120, 0.1);
      color: var(--ink);
    }
    .stage-chip.done {
      border-color: rgba(36, 122, 72, 0.24);
      background: rgba(36, 122, 72, 0.08);
      color: #20563a;
    }
    .builder-stage-nav {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
    }
    .builder-stage-nav button {
      min-height: 32px;
      border-radius: 999px;
      background: #f9fbfc;
      color: var(--muted);
      border: 1px solid #d4dce8;
      padding: 6px 12px;
      box-shadow: none;
    }
    .builder-stage-nav button.active {
      border-color: rgba(18, 78, 120, 0.28);
      background: rgba(18, 78, 120, 0.1);
      color: var(--ink);
    }
    .builder-workspace-layout {
      display: grid;
      grid-template-columns: 292px minmax(0, 1fr);
      gap: 16px;
      align-items: start;
      margin-top: 12px;
    }
    .builder-side-stack {
      display: grid;
      gap: 12px;
    }
    .builder-stage-sidebar .builder-stage-nav {
      display: grid;
      gap: 8px;
      margin-top: 12px;
    }
    .builder-stage-sidebar .builder-stage-nav button {
      width: 100%;
      min-height: 44px;
      border-radius: 12px;
      padding: 10px 12px;
      justify-content: flex-start;
      text-align: left;
    }
    .builder-stage-sidebar .builder-mode-shell {
      margin-top: 0;
    }
    .builder-stage-sidebar .segment-control {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }
    .builder-stage-sidebar .builder-mode-note {
      margin-top: 10px;
    }
    .builder-main-stack {
      display: grid;
      gap: 16px;
      min-width: 0;
    }
    .builder-stage-content {
      margin-bottom: 0;
    }
    .builder-stage-panel[hidden] {
      display: none !important;
    }
    .editor-workbench-panel[hidden] {
      display: none !important;
    }
    .builder-step-title {
      margin: 0 0 10px;
      font-size: 15px;
      font-weight: 700;
    }
    .metric-overview {
      display: grid;
      gap: 8px;
      margin-top: 12px;
    }
    .metric-row {
      display: grid;
      grid-template-columns: 78px minmax(0, 1fr) 72px;
      gap: 8px;
      align-items: center;
      font-size: 12px;
    }
    .metric-label {
      color: var(--muted);
      white-space: nowrap;
    }
    .metric-value {
      color: var(--muted);
      text-align: right;
      white-space: nowrap;
    }
    .metric-bar {
      height: 10px;
      border-radius: 999px;
      background: #dce7ef;
      overflow: hidden;
    }
    .metric-bar > span {
      display: block;
      height: 100%;
      border-radius: 999px;
      background: linear-gradient(90deg, #4d94b8, #124e78);
      min-width: 6px;
    }
    .trend-panel {
      margin-top: 12px;
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 10px 12px;
      background: #fbfcfe;
    }
    .trend-panel strong {
      display: block;
      font-size: 13px;
      margin-bottom: 4px;
    }
    .trend-panel p {
      margin: 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
    .trend-svg {
      width: 100%;
      height: 104px;
      display: block;
      margin-top: 10px;
    }
    .trend-caption {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
      margin-top: 8px;
      color: var(--muted);
      font-size: 11px;
    }
    .alert-strip {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 10px;
    }
    .alert-pill {
      display: inline-flex;
      align-items: center;
      min-height: 28px;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 12px;
      border: 1px solid #d4dce8;
      background: #f9fbfc;
      color: var(--muted);
    }
    .alert-pill.good {
      border-color: rgba(36, 122, 72, 0.24);
      background: rgba(36, 122, 72, 0.08);
      color: #20563a;
    }
    .alert-pill.warn {
      border-color: rgba(185, 106, 16, 0.26);
      background: rgba(185, 106, 16, 0.08);
      color: #7d4a11;
    }
    .alert-pill.bad {
      border-color: rgba(171, 45, 45, 0.24);
      background: rgba(171, 45, 45, 0.08);
      color: #8a2d2d;
    }
    .field-help {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
    .status {
      min-height: 22px;
      color: var(--muted);
    }
    .ok { color: var(--ok); }
    .bad { color: var(--bad); }
    .cards {
      display: grid;
      grid-template-columns: repeat(3, minmax(120px, 1fr));
      gap: 10px;
    }
    .card, .summary-card {
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px;
      background: linear-gradient(180deg, #ffffff, #f8fbfc);
    }
    .card span, .summary-card span {
      display: block;
      color: var(--muted);
      font-size: 12px;
    }
    .card strong, .summary-card strong {
      display: block;
      font-size: 21px;
      margin-top: 4px;
    }
    .summary-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(140px, 1fr));
      gap: 10px;
      margin-bottom: 12px;
    }
    .detail-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 12px;
    }
    .detail-box {
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 10px 12px;
      background: #fbfcfe;
    }
    .detail-box strong {
      display: block;
      margin-bottom: 3px;
      font-size: 13px;
    }
    .quick-edit-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin-top: 10px;
    }
    .quick-edit-grid label {
      display: grid;
      gap: 6px;
      font-size: 12px;
      color: var(--muted);
    }
    .quick-edit-grid input,
    .quick-edit-grid select {
      min-height: 36px;
    }
    .quick-edit-section {
      border: 1px solid #e4eaf2;
      border-radius: 12px;
      padding: 10px;
      background: #fff;
    }
    .quick-edit-section strong {
      display: block;
      font-size: 13px;
      margin-bottom: 4px;
    }
    .quick-edit-section p {
      margin: 0 0 10px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
    .chips {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin: 10px 0 0;
    }
    .chip {
      display: inline-flex;
      align-items: center;
      min-height: 28px;
      padding: 4px 10px;
      border-radius: 999px;
      border: 1px solid #d4dce8;
      background: #f9fbfc;
      color: var(--muted);
      font-size: 12px;
    }
    .files {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 10px;
    }
    .files a {
      display: inline-flex;
      align-items: center;
      min-height: 30px;
      padding: 4px 10px;
      border-radius: 999px;
      border: 1px solid #d6dee8;
      background: #fff;
    }
    .preview-shell {
      border: 1px solid var(--line);
      border-radius: 14px;
      overflow: hidden;
      background: #fff;
    }
    .preview-meta {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      background: #f8fafc;
    }
    iframe {
      width: 100%;
      height: 720px;
      border: 0;
      display: block;
      background: #fff;
    }
    .empty {
      border: 1px dashed var(--line);
      border-radius: 12px;
      padding: 18px;
      color: var(--muted);
      background: #fbfcfe;
    }
    .fold-panel {
      border: 1px solid #e4eaf2;
      border-radius: 12px;
      background: #fbfcfe;
      margin-top: 10px;
      overflow: hidden;
    }
    .fold-panel summary {
      list-style: none;
      cursor: pointer;
      padding: 12px 14px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      font-weight: 700;
    }
    .fold-panel summary::-webkit-details-marker {
      display: none;
    }
    .fold-panel summary span {
      color: var(--muted);
      font-size: 12px;
      font-weight: 500;
    }
    .fold-panel summary::after {
      content: '展开';
      color: var(--accent);
      font-size: 12px;
      font-weight: 700;
      flex-shrink: 0;
    }
    .fold-panel[open] summary::after {
      content: '收起';
    }
    .fold-panel-body {
      padding: 0 14px 14px;
      border-top: 1px solid #e7edf5;
    }
    .mini-table {
      margin-top: 10px;
      border: 1px solid var(--line);
      border-radius: 12px;
      overflow: hidden;
    }
    .compare-toolbar {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      margin-bottom: 12px;
    }
    .compare-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      margin-bottom: 12px;
    }
    .compare-figure {
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px;
      background: #fff;
      margin-top: 10px;
    }
    .compare-figure strong {
      display: block;
      font-size: 13px;
    }
    .compare-figure p {
      margin: 6px 0 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }
    .compare-card {
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px;
      background: linear-gradient(180deg, #ffffff, #f8fbfc);
    }
    .compare-card h3 {
      margin: 0 0 10px;
      font-size: 14px;
    }
    .compare-metrics {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }
    .compare-metrics div {
      border: 1px solid #e4eaf2;
      border-radius: 10px;
      padding: 8px;
      background: #fff;
    }
    .compare-metrics span {
      display: block;
      color: var(--muted);
      font-size: 12px;
    }
    .compare-chart {
      width: 100%;
      height: 220px;
      display: block;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #fbfcfe;
      margin-top: 10px;
    }
    .replay-toolbar {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto auto;
      gap: 10px;
      align-items: end;
      margin-bottom: 12px;
    }
    .replay-stage {
      border: 1px solid var(--line);
      border-radius: 14px;
      overflow: hidden;
      background:
        radial-gradient(circle at top, rgba(15, 108, 123, 0.18), rgba(15, 108, 123, 0) 46%),
        linear-gradient(180deg, #fbfdff, #edf3f7);
    }
    .replay-canvas {
      width: 100%;
      height: 300px;
      display: grid;
      place-items: center;
      perspective: 1000px;
      overflow: hidden;
      position: relative;
    }
    .replay-canvas::before {
      content: "";
      position: absolute;
      inset: auto 10% 24px 10%;
      height: 120px;
      background: radial-gradient(ellipse at center, rgba(18, 78, 120, 0.18), rgba(18, 78, 120, 0) 68%);
      pointer-events: none;
    }
    .replay-world {
      width: 260px;
      height: 220px;
      position: relative;
      transform-style: preserve-3d;
      transform: rotateX(-18deg) rotateZ(4deg);
    }
    .replay-grid {
      position: absolute;
      inset: 64% 2% -8% 2%;
      border-radius: 50%;
      border: 1px solid rgba(18, 78, 120, 0.16);
      transform: rotateX(78deg);
      box-shadow:
        0 0 0 24px rgba(18, 78, 120, 0.05),
        0 0 0 48px rgba(18, 78, 120, 0.035);
    }
    .satellite3d {
      position: absolute;
      left: 50%;
      top: 50%;
      width: 82px;
      height: 82px;
      transform-style: preserve-3d;
      transform: translate3d(-50%, -50%, 0);
    }
    .sat-body-face {
      position: absolute;
      inset: 0;
      border: 1px solid rgba(23, 32, 42, 0.22);
      background: linear-gradient(135deg, #f9fbfd, #dfe9ef);
      opacity: 0.96;
    }
    .face-front { transform: translateZ(24px); }
    .face-back { transform: rotateY(180deg) translateZ(24px); }
    .face-right { transform: rotateY(90deg) translateZ(24px); width: 48px; left: 17px; background: linear-gradient(135deg, #d6e2ea, #c5d5df); }
    .face-left { transform: rotateY(-90deg) translateZ(24px); width: 48px; left: 17px; background: linear-gradient(135deg, #d6e2ea, #c5d5df); }
    .face-top { transform: rotateX(90deg) translateZ(24px); height: 48px; top: 17px; background: linear-gradient(135deg, #ffffff, #e8f0f4); }
    .face-bottom { transform: rotateX(-90deg) translateZ(24px); height: 48px; top: 17px; background: linear-gradient(135deg, #d7e3eb, #c6d5de); }
    .panel3d {
      position: absolute;
      top: 26px;
      width: 94px;
      height: 28px;
      border: 1px solid rgba(18, 78, 120, 0.35);
      background:
        repeating-linear-gradient(90deg, rgba(255,255,255,0.16), rgba(255,255,255,0.16) 9px, transparent 9px, transparent 18px),
        linear-gradient(135deg, #124e78, #0f6c7b);
      box-shadow: inset 0 0 0 1px rgba(255,255,255,0.14);
      transform: translateZ(3px);
    }
    .panel-left { left: -96px; transform-origin: right center; transform: rotateY(-8deg) translateZ(3px); }
    .panel-right { right: -96px; transform-origin: left center; transform: rotateY(8deg) translateZ(3px); }
    .boresight3d {
      position: absolute;
      left: 34px;
      top: -14px;
      width: 0;
      height: 0;
      border-left: 7px solid transparent;
      border-right: 7px solid transparent;
      border-bottom: 22px solid var(--warm);
      transform: translateZ(18px);
      filter: drop-shadow(0 4px 8px rgba(185, 106, 16, 0.28));
    }
    .replay-readout {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 10px;
      padding: 12px;
      border-top: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.9);
    }
    .replay-readout div {
      border: 1px solid #e4eaf2;
      border-radius: 10px;
      padding: 8px 10px;
      background: #fff;
    }
    .replay-readout span {
      display: block;
      color: var(--muted);
      font-size: 12px;
    }
    .replay-slider {
      width: 100%;
      margin-top: 12px;
    }
    .timeline-strip {
      margin-top: 12px;
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 10px;
      background: #fbfcfe;
    }
    .timeline-legend {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 8px;
    }
    .timeline-bar {
      position: relative;
      height: 20px;
      border-radius: 999px;
      background: #e8eef4;
      overflow: hidden;
    }
    .timeline-segment {
      position: absolute;
      top: 0;
      bottom: 0;
      min-width: 2px;
      opacity: 0.95;
      cursor: pointer;
    }
    .timeline-cursor {
      position: absolute;
      top: -3px;
      width: 2px;
      bottom: -3px;
      background: #17202a;
      box-shadow: 0 0 0 2px rgba(255,255,255,0.5);
    }
    .runtime-strip {
      margin-top: 12px;
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 10px;
      background: #fbfcfe;
    }
    .runtime-strip h3 {
      margin: 0 0 8px;
      font-size: 13px;
    }
    .runtime-strip p {
      margin: 0 0 8px;
      color: var(--muted);
      font-size: 12px;
    }
    .runtime-modules {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    .runtime-modules .chip {
      min-height: 26px;
      font-size: 11px;
    }
    .editor-meta {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
      margin-bottom: 10px;
      flex-wrap: wrap;
    }
    .editor-toolbar {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      align-items: center;
      margin-bottom: 10px;
    }
    .editor-area {
      width: 100%;
      min-height: 320px;
      resize: vertical;
      border-radius: 12px;
      border: 1px solid var(--line);
      padding: 12px;
      background: #fbfcfe;
      color: var(--ink);
      font: 13px/1.55 Consolas, "Cascadia Mono", "Courier New", monospace;
    }
    .editor-help {
      margin-top: 10px;
      color: var(--muted);
      font-size: 12px;
    }
    a {
      color: var(--accent-strong);
      text-decoration: none;
    }
    a:hover { text-decoration: underline; }
    @media (max-width: 1080px) {
      .sidebar {
        position: static;
        inset: auto;
        width: auto;
        height: auto;
        overflow: visible;
        border-right: 0;
        border-bottom: 1px solid rgba(255, 255, 255, 0.06);
      }
      .content-shell {
        margin-left: 0;
      }
      .hero, .grid, .cards, .form-grid, .summary-grid, .detail-grid, .compare-toolbar, .compare-grid, .intro-grid, .process-strip, .template-grid, .history-grid, .experiment-blueprint-grid, .library-shell, .library-detail-grid, .diagram-grid, .flow-sequence, .workbench-hero-grid {
        grid-template-columns: 1fr;
      }
      .overview-entry-grid {
        grid-template-columns: 1fr;
      }
      .workspace-layout {
        grid-template-columns: 1fr;
      }
      .workspace-sidecard {
        position: static;
      }
      .quick-select-grid {
        grid-template-columns: 1fr;
      }
      header { padding: 16px 14px 8px; }
      main { padding: 12px 14px 30px; }
      iframe { height: 520px; }
    }
  </style>
</head>
<body>
  <div class="app-shell">
  <aside class="sidebar">
    <div class="sidebar-brand">
      <div class="sidebar-badge">SM</div>
      <div>
        <strong>satmodel 平台</strong>
        <span>卫星姿态控制仿真工作区。把实验设计、计划管理和结果浏览组织成一个可展示、可复查的本地平台。</span>
      </div>
    </div>
    <div class="sidebar-section-label">Platform</div>
    <div class="sidebar-nav" id="sidebar-nav">
      <a class="active" href="?page=overview#page=overview" data-page="overview">平台总览<span>平台介绍、实验范围、路线资料</span></a>
      <a href="?page=lab&lab=library#page=lab&lab=library" data-page="lab" data-lab-view="library">实验库<span>成熟实验模板、示例入口、实验地图</span></a>
      <a href="?page=lab&lab=builder#page=lab&lab=builder" data-page="lab" data-lab-view="builder">创建实验<span>场景、变量、任务和验收的一站式配置</span></a>
      <a href="?page=lab&lab=manage&manage=pool#page=lab&lab=manage&manage=pool" data-page="lab" data-lab-view="manage">计划管理<span>场景浏览、计划编辑、归档和批量校验</span></a>
      <a href="?page=results&results=overview#page=results&results=overview" data-page="results" data-results-view="overview">结果总览<span>结果目录、通过率、运行状态和最佳 run</span></a>
      <a href="?page=results&results=compare#page=results&results=compare" data-page="results" data-results-view="compare">结果对比<span>关键 run 对比、参数差异和曲线检查</span></a>
      <a href="?page=results&results=replay#page=results&results=replay" data-page="results" data-results-view="replay">姿态回放<span>动画回放、模式时间线和 runtime 快照</span></a>
      <a href="?page=results&results=preview#page=results&results=preview" data-page="results" data-results-view="preview">Dashboard 预览<span>独立结果界面的内嵌预览与打开入口</span></a>
    </div>
    <div class="sidebar-meta">
      <strong>当前 UI 构建</strong>
      <span>__UI_BUILD_STAMP__ · satmodel __SATMODEL_VERSION__</span>
    </div>
  </aside>
  <div class="content-shell">
  <header>
    <div class="hero">
      <div>
        <p class="eyebrow">satmodel platform workspace</p>
        <h1>卫星姿态控制仿真平台控制台</h1>
        <p class="lead">把场景、实验计划、批量运行和结果预览收在同一个本地界面里，方便我们逐步把研究脚本打磨成真正的平台工作流。</p>
      </div>
      <div class="hero-panel">
        <span>当前工作区</span>
        <strong id="workspace">读取中...</strong>
      </div>
    </div>
  </header>
  <main>
    <div class="page-nav" id="page-nav" hidden aria-hidden="true">
      <a class="active" href="?page=overview#page=overview" data-page="overview">总览</a>
      <a href="?page=lab&lab=library#page=lab&lab=library" data-page="lab" data-lab-view="library">实验设计</a>
      <a href="?page=results&results=overview#page=results&results=overview" data-page="results" data-results-view="overview">运行结果</a>
    </div>
    <div class="page-summary" id="page-summary">
      <div id="page-summary-copy"><strong>当前视图：总览。</strong> 先看平台定位、实验范围和最近进展，再进入实验设计与结果工作台。</div>
      <div class="status" id="status" style="margin-top:10px">就绪。</div>
    </div>
    <div class="page-view" data-page-view="overview">
      <section>
        <h2>平台总览工作台</h2>
        <div class="editor-view-shell" style="margin-bottom:12px">
          <div class="segment-control" id="overview-workbench-toggle">
            <button class="active" type="button" data-overview-workbench-view="position">平台定位</button>
            <button type="button" data-overview-workbench-view="route">实验主线</button>
            <button type="button" data-overview-workbench-view="assets">当前资产</button>
          </div>
          <div class="editor-view-note" id="overview-workbench-summary">默认先看平台定位，先确认平台当前在解决什么问题、工作流如何闭环，再进入实验主线和工作区资产。</div>
        </div>
        <div class="workspace-panel-view" data-overview-workbench-view="position">
          <div id="overview-workbench" style="margin-bottom:12px">
            <div class="callout">
              <strong>平台总览台</strong>
              <p>这里会把平台定位、实验主线和当前资产固定成一个首页驾驶舱。</p>
            </div>
          </div>
          <div class="workbench-hero-grid">
            <div class="section-stack">
              <div class="summary-grid" id="overview-position-signals">
                <div class="summary-card"><span>当前阶段</span><strong>读取中</strong></div>
                <div class="summary-card"><span>主线覆盖</span><strong>读取中</strong></div>
                <div class="summary-card"><span>资产状态</span><strong>读取中</strong></div>
                <div class="summary-card"><span>下一动作</span><strong>读取中</strong></div>
              </div>
              <div class="history-detail" style="margin-bottom:0">
                <strong>首页五步入口</strong>
                <p>首页先保留五个最核心动作，让第一次进入平台的人直接知道下一步点哪里。</p>
                <div class="process-strip">
                  <div class="process-step">
                    <button class="process-step-action" type="button" onclick="switchLabView('library')">
                      <span>第一步</span>
                      <strong>选择场景</strong>
                      <p>先从实验库选择成熟实验入口，避免从空白计划直接开始。</p>
                    </button>
                  </div>
                  <div class="process-step">
                    <button class="process-step-action" type="button" onclick="switchLabView('builder')">
                      <span>第二步</span>
                      <strong>创建实验</strong>
                      <p>按研究问题、变量、任务和验收把实验固化成可复现计划。</p>
                    </button>
                  </div>
                  <div class="process-step">
                    <button class="process-step-action" type="button" onclick="switchManageView('editor')">
                      <span>第三步</span>
                      <strong>校验并运行</strong>
                      <p>在计划管理里校验、运行并沉淀 README、index 和 dashboard。</p>
                    </button>
                  </div>
                  <div class="process-step">
                    <button class="process-step-action" type="button" onclick="switchResultsView('overview')">
                      <span>第四步</span>
                      <strong>浏览结果</strong>
                      <p>先看通过率、焦点结果和风险结果，再决定读图顺序。</p>
                    </button>
                  </div>
                  <div class="process-step">
                    <button class="process-step-action" type="button" onclick="switchResultsView('compare')">
                      <span>第五步</span>
                      <strong>回放与对比</strong>
                      <p>把关键 run 送到对比和姿态回放，形成下一轮实验判断。</p>
                    </button>
                  </div>
                </div>
              </div>
              <div class="intro-grid">
                <div class="callout" style="margin-bottom:0">
                  <strong>什么是实验</strong>
                  <p>场景描述一次仿真的基础物理与控制配置；实验计划则在场景之上增加参数扫描、Monte Carlo、任务模板、输出目录和验收组织，用于形成可复现的 run 集合。</p>
                </div>
                <div class="callout" style="margin-bottom:0">
                  <strong>实验会产出什么</strong>
                  <p>每次实验会生成标准结果目录，包括 `README.md`、`index.json`、`summary_metrics.csv`、`dashboard.html`，以及每个 run 的时序、指标和事件文件，方便展示、复查和后续扩展。</p>
                </div>
              </div>
            </div>
            <div class="section-stack">
              <div id="overview-priority-route">
                <div class="history-detail" style="margin-bottom:0">
                  <strong>主线入口建议</strong>
                  <p>这里会显示当前最适合先跑、先补和先展示的实验入口。</p>
                </div>
              </div>
              <div class="history-detail" style="margin-bottom:0">
                <strong>资料与扩展入口</strong>
                <p>先读平台资料，再决定往架构、实验资产还是界面继续扩展。</p>
                <div class="detail-grid" style="margin-top:10px">
                  <div class="detail-box">
                    <strong>核心资料</strong>
                    <div>平台路线、架构分层、项目总说明和实验库建议都整理在 `docs/` 里，适合快速上手、做汇报或继续推进平台化。</div>
                  </div>
                  <div class="detail-box">
                    <strong>可插拔扩展位</strong>
                    <div>当前最适合继续扩的接口是 `ExperimentPlan`、runtime 模板、结果报告构建和 dashboard 面板。先沿这些边界扩，比直接把逻辑塞进单个脚本更稳。</div>
                  </div>
                </div>
                <div class="toolbar" style="margin-top:10px">
                  <a href="/file/docs/PLATFORM_PLAN.md" target="_blank">平台路线</a>
                  <a href="/file/docs/ARCHITECTURE.md" target="_blank">架构说明</a>
                  <a href="/file/docs/PROJECT_GUIDE.md" target="_blank">项目总说明</a>
                  <a href="/file/docs/EXPERIMENT_LIBRARY.md" target="_blank">实验库建议</a>
                  <a href="/file/docs/REFERENCES.md" target="_blank">参考范式</a>
                  <a href="/file/docs/PLATFORM_UI_GUIDE.md" target="_blank">界面说明</a>
                </div>
              </div>
            </div>
          </div>
        </div>
        <div class="workspace-panel-view" data-overview-workbench-view="route" hidden>
          <section>
            <h3>平台工作流图</h3>
            <div id="overview-platform-map" class="flow-sequence"></div>
            <div class="diagram-grid">
              <div id="overview-architecture-map" class="diagram-card"></div>
              <div id="overview-experiment-map" class="diagram-card"></div>
            </div>
          </section>
          <section>
            <h3>标准实验路径</h3>
            <div class="callout">
              <strong>推荐演示主线</strong>
              <p>平台内置实验已经足够串成一条固定主线。先用这条路径做演示和收敛，再在每一步派生自己的实验，会比从空白计划直接起步更稳定。</p>
            </div>
            <div id="overview-standard-route" style="margin-top:10px"></div>
          </section>
        </div>
        <div class="workspace-panel-view" data-overview-workbench-view="assets" hidden>
          <div class="cards" id="overview-cards">
            <div class="card"><span>场景库</span><strong>0</strong></div>
            <div class="card"><span>实验计划</span><strong>0</strong></div>
            <div class="card"><span>结果界面</span><strong>0</strong></div>
          </div>
          <div class="intro-grid" style="margin-top:10px">
            <div id="overview-latest-plan" class="history-detail" style="margin-bottom:0"></div>
            <div id="overview-latest-result" class="history-detail" style="margin-bottom:0"></div>
          </div>
          <div id="overview-attention" style="margin-top:10px"></div>
          <div class="grid" style="margin-top:10px">
            <div>
              <section>
                <h3>实验内容建议</h3>
                <div class="intro-grid">
                  <div class="callout" style="margin-bottom:0">
                    <strong>优先补强的实验主题</strong>
                    <p>建议优先围绕控制器整定、鲁棒性、任务模式切换和执行器能力边界补实验，而不是先堆太多高保真但难验证的模型。</p>
                  </div>
                  <div class="callout" style="margin-bottom:0">
                    <strong>为什么先做实验工作流</strong>
                    <p>把实验命名、变量说明、验收门限、结果目录和回放入口先标准化，后面无论接多速率、高保真还是数据库都会顺很多。</p>
                  </div>
                </div>
              </section>
            </div>
            <div>
              <section>
                <h3>工作区概览</h3>
                <div class="cards">
                  <div class="card"><span>场景</span><strong id="scenario-count">0</strong></div>
                  <div class="card"><span>实验计划</span><strong id="experiment-count">0</strong></div>
                  <div class="card"><span>已归档计划</span><strong id="archived-experiment-count">0</strong></div>
                  <div class="card"><span>结果界面</span><strong id="dashboard-count">0</strong></div>
                </div>
              </section>
            </div>
          </div>
        </div>
      </section>
    </div>
    <div class="page-view" data-page-view="lab" hidden>
      <div class="workspace-shell">
        <div class="workspace-summary" id="lab-summary">
          <div id="lab-summary-copy"><strong>当前工作台：实验库。</strong> 先选一个成熟实验范式，看它在回答什么问题、关注什么指标，再进入创建或管理。</div>
        </div>
        <div class="workspace-layout">
          <aside class="workspace-sidecard">
            <strong>实验设计导航</strong>
            <p>先在这里切换工作区。进入“实验库”可挑成熟实验，进入“创建实验”可新建计划，进入“计划管理”可编辑和归档已有实验。</p>
            <div class="workspace-nav workspace-nav-vertical" id="lab-nav">
              <button class="active" type="button" data-lab-view="library">实验库<span>先选成熟实验模板和问题主线</span></button>
              <button type="button" data-lab-view="builder">创建实验<span>配置场景、变量、任务和验收标准</span></button>
              <button type="button" data-lab-view="manage">计划管理<span>编辑、复制、归档和批量校验已有计划</span></button>
            </div>
            <div class="workspace-nav-note" id="lab-nav-note">当前建议先进入“实验库”，从成熟实验开始，再决定是直接复用还是创建自己的变体计划。</div>
          </aside>
          <div class="workspace-content">
            <div class="workspace-view" data-lab-view="library">
              <div class="editor-view-shell" style="margin-bottom:12px">
                <div class="segment-control" id="library-workspace-toggle">
                  <button class="active" type="button" data-library-view="route">实验主线</button>
                  <button type="button" data-library-view="map">实验地图</button>
                  <button type="button" data-library-view="demo">示例入口</button>
                  <button type="button" data-library-view="template">模板库</button>
                </div>
                <div class="editor-view-note" id="library-workspace-summary">默认先看实验主线，先理解平台当前推荐的研究路线，再决定直接跑示例还是进入具体模板。</div>
              </div>
              <div class="workspace-panel-view" data-library-view="route">
                <div class="single-column-grid">
                  <section>
                    <h2>实验资产地图</h2>
                    <div class="intro-grid">
                      <div class="callout" style="margin-bottom:0">
                        <strong>实验建设主线</strong>
                        <p>当前平台按“闭环基线 -> 控制器比较 -> 统计鲁棒性 -> 环境与扰动 -> 任务模式 -> 执行器边界 -> 验收收口”的七阶段主线补实验。这样更接近 docs 里的标准研究节奏，也更容易把计划、结果和展示资产串成同一条链。</p>
                      </div>
                      <div class="callout" style="margin-bottom:0">
                        <strong>为什么先补实验本身</strong>
                        <p>场景、变量、验收和结果目录先标准化，后面再接多速率、高保真、数据库或可视化面板时，实验资产不会散掉。</p>
                      </div>
                    </div>
                    <div class="experiment-blueprint-grid" style="margin-top:10px">
                      <div class="experiment-blueprint">
                        <span>实验类型</span>
                        <strong>控制器整定</strong>
                        <p>比较 `pd_kp`、`pd_kd` 等控制参数对收敛速度、稳态误差和峰值力矩的影响。</p>
                      </div>
                      <div class="experiment-blueprint">
                        <span>实验类型</span>
                        <strong>随机鲁棒性</strong>
                        <p>比较随机种子、噪声和初值变化对通过率、最优/最差 run 差距和误差分布的影响。</p>
                      </div>
                      <div class="experiment-blueprint">
                        <span>实验类型</span>
                        <strong>任务模式切换</strong>
                        <p>比较 detumble、惯性保持、太阳指向、对地指向等模式在切换过程中的姿态过渡表现。</p>
                      </div>
                      <div class="experiment-blueprint">
                        <span>实验类型</span>
                        <strong>执行器能力边界</strong>
                        <p>比较反作用轮力矩上限、饱和风险和轮速变化趋势对控制性能的约束效果。</p>
                      </div>
                    </div>
                  </section>
                  <section>
                    <h2>标准实验路径</h2>
                    <div class="callout">
                      <strong>从主线开始</strong>
                      <p>先沿固定主线演示平台：闭环基线、控制器比较、统计鲁棒性、环境与任务，再到执行器边界和验收收口。每一步都给出直接可运行的入口。</p>
                    </div>
                    <div id="library-standard-route" style="margin-top:10px"></div>
                  </section>
                </div>
              </div>
              <div class="workspace-panel-view" data-library-view="map" hidden>
                <section>
                  <h2>实验地图</h2>
                  <div class="callout">
                    <strong>协议、实验与资产一张图</strong>
                    <p>这里按协议阶段统一展示“这个实验回答什么问题、当前有没有代表计划、有没有代表结果、下一步往哪里接”。适合先建立整体实验认知，再进入模板细节。</p>
                  </div>
                  <div id="library-protocol-map" style="margin-top:10px"></div>
                </section>
              </div>
              <div class="workspace-panel-view" data-library-view="demo" hidden>
                <section>
                  <h2>一键示例运行</h2>
                  <div class="callout">
                    <strong>示例入口</strong>
                    <p>这里适合第一次演示平台时直接跑出结果图和动画。先用示例确认流程，再进入“创建实验”做自己的变量和任务设计。</p>
                  </div>
                  <div id="quick-demo-grid" style="margin-top:10px"></div>
                  <div id="quick-demo-status" style="margin-top:10px"></div>
                </section>
              </div>
              <div class="workspace-panel-view" data-library-view="template" hidden>
                <section>
                  <h2>推荐实验库</h2>
                  <div class="callout">
                    <strong>当前成熟实验</strong>
                    <p>这些计划已经按场景、变量、任务和验收做过整理，适合直接打开、复用或作为新的实验分支起点。</p>
                  </div>
                  <div id="experiment-library-grid" style="margin-top:10px"></div>
                </section>
              </div>
            </div>
            <div class="workspace-view" data-lab-view="builder" hidden>
          <section id="builder-section">
            <h2>实验设计工作台</h2>
            <div class="history-detail" style="margin-bottom:10px">
              <strong>创建实验的默认路径</strong>
              <p>先用模板锁定研究问题，再定义主变量与任务验收，最后确认输出并生成计划。默认页面只保留常用字段，二级说明和高级配置按需展开。</p>
            </div>
            <div class="builder-workspace-layout">
              <aside class="workspace-sidecard builder-stage-sidebar">
                <div class="builder-side-stack">
                  <details class="fold-panel">
                    <summary>实验前置说明<span>查看平台对实验建设方向的建议</span></summary>
                    <div class="fold-panel-body">
                      <div class="intro-grid" style="margin-top:10px">
                        <div class="callout" style="margin-bottom:0">
                          <strong>实验说明</strong>
                          <p>场景定义单次仿真的基础模型，实验计划则在场景上增加参数扫描、Monte Carlo、任务模式和输出目录，用来组织可复现的批量实验。</p>
                        </div>
                        <div class="callout" style="margin-bottom:0">
                          <strong>当前建议的实验补强方向</strong>
                          <p>优先沿七阶段主线补实验：先闭环基线和控制器比较，再做鲁棒性、环境、任务、执行器边界，最后回到验收收口。这样更容易形成对比清楚、报告完整、可持续扩展的实验资产。</p>
                        </div>
                      </div>
                    </div>
                  </details>
                  <div class="builder-mode-shell">
                    <div class="segment-control" id="builder-view-toggle">
                      <button class="active" type="button" data-builder-view="basic">快速配置</button>
                      <button type="button" data-builder-view="advanced">高级配置</button>
                    </div>
                    <div class="builder-mode-note" id="builder-view-summary">默认先显示常用配置，保持界面简洁；需要更细粒度控制时再展开高级项。</div>
                  </div>
                  <div class="builder-stage-nav" id="builder-stage-nav">
                    <button class="active" type="button" data-builder-stage-target="question">1. 研究问题</button>
                    <button type="button" data-builder-stage-target="variables">2. 场景与变量</button>
                    <button type="button" data-builder-stage-target="runtime">3. 任务与验收</button>
                    <button type="button" data-builder-stage-target="review">4. 预览与生成</button>
                  </div>
                  <div class="callout info" id="builder-stage-summary" style="margin-top:0">
                    <strong>当前步骤：研究问题</strong>
                    <p>先确定实验要回答什么问题，再选择基线场景和推荐模板。这样后面的变量设计和结果报告会更清楚。</p>
                  </div>
                  <div class="editor-view-shell" style="margin-top:0">
                    <div class="segment-control" id="builder-workbench-toggle">
                      <button class="active" type="button" data-builder-workbench-view="focus">设计焦点</button>
                      <button type="button" data-builder-workbench-view="scale">实验规模</button>
                      <button type="button" data-builder-workbench-view="deliverables">结果出口</button>
                    </div>
                    <div class="editor-view-note" id="builder-workbench-summary-copy">默认先看设计焦点，先确认当前研究问题、协议阶段和下一步动作，再进入字段级配置。</div>
                  </div>
                  <div id="builder-workbench-summary">
                    <div class="callout">
                      <strong>实验设计驾驶舱</strong>
                      <p>这里会把当前实验的焦点问题、实验规模和结果出口固定成一个工作台摘要。</p>
                    </div>
                  </div>
                </div>
              </aside>
              <div class="workspace-content builder-main-stack">
                <div class="builder-stage-panel builder-stage-content" data-builder-stage="question">
              <div class="builder-step-title">研究问题与基线场景</div>
              <div class="form-grid">
                <label class="full">场景
                  <select id="builder-scenario"></select>
                  <span class="field-help" id="builder-scenario-help">选择实验基线场景。它决定动力学、控制器、环境、初始状态和默认输出设置。</span>
                </label>
                <label>实验名称
                  <input id="builder-name" placeholder="quick_pd_sweep">
                  <span class="field-help">实验计划名称，会显示在实验列表和结果摘要里，也会参与生成默认文件名。</span>
                </label>
                <label class="full">实验说明
                  <textarea id="builder-description" class="builder-textarea" placeholder="例如：比较 PD 增益变化对收敛速度、末端误差和控制力矩的影响。"></textarea>
                  <span class="field-help">写清楚这个实验要回答什么问题，后续结果报告、计划管理和平台展示都会更清楚。</span>
                </label>
              </div>
                <div class="callout" style="margin-top:10px">
                  <strong>推荐实验模板</strong>
                  <p>先选一个成熟实验范式作为起点，再在后续步骤中补具体变量、任务和验收。这样更符合成熟仿真平台“模板化复用”的工作方式。</p>
                </div>
                <div class="builder-category-shell">
                  <strong>先选实验主线</strong>
                  <div class="segment-control" id="builder-category-nav" style="margin-top:10px">
                    <button class="active" type="button" data-builder-category="all">全部</button>
                    <button type="button" data-builder-category="tuning">整定</button>
                    <button type="button" data-builder-category="benchmark">Benchmark</button>
                    <button type="button" data-builder-category="robustness">鲁棒性</button>
                    <button type="button" data-builder-category="environment">环境</button>
                    <button type="button" data-builder-category="mission">任务</button>
                    <button type="button" data-builder-category="actuator">执行器</button>
                    <button type="button" data-builder-category="sensing">测量</button>
                    <button type="button" data-builder-category="acceptance">验收</button>
                  </div>
                  <div class="builder-category-copy" id="builder-category-copy">先按研究问题选择实验主线，再挑一个具体模板。这样变量、任务和结果解释会更像成熟平台里的实验流程。</div>
                </div>
                <div class="template-grid" id="builder-template-grid">
                <button class="template-card" type="button" data-template="pd_tuning" data-builder-category="tuning">
                  <span>快速模板</span>
                  <strong>PD 参数整定</strong>
                  <p>扫描 `pd_kp`，用于快速比较收敛速度、稳态误差和控制力矩变化。</p>
                </button>
                <button class="template-card" type="button" data-template="mc_robustness" data-builder-category="robustness">
                  <span>快速模板</span>
                  <strong>随机鲁棒性</strong>
                  <p>扫描随机种子并叠加 Monte Carlo，适合看噪声和初始化敏感性。</p>
                </button>
                <button class="template-card" type="button" data-template="sun_transition" data-builder-category="mission">
                  <span>快速模板</span>
                  <strong>太阳指向切换</strong>
                  <p>先消旋再进入 `sun_pointing`，适合演示任务模式切换和姿态回放。</p>
                </button>
                <button class="template-card" type="button" data-template="earth_transition" data-builder-category="mission">
                  <span>快速模板</span>
                  <strong>对地指向切换</strong>
                  <p>先消旋再进入 `earth_pointing`，适合演示对地任务、nadir 参考和任务时间线。</p>
                </button>
                <button class="template-card" type="button" data-template="wheel_capability" data-builder-category="actuator">
                  <span>快速模板</span>
                  <strong>执行器能力对比</strong>
                  <p>扫描轮组最大力矩，适合比较执行机构约束对性能和饱和的影响。</p>
                </button>
                <button class="template-card" type="button" data-template="controller_benchmark" data-builder-category="benchmark">
                  <span>快速模板</span>
                  <strong>控制器基准对比</strong>
                  <p>在同一场景中比较 `PD` 和 `LADRC`，适合形成第一版控制器 benchmark。</p>
                </button>
                <button class="template-card" type="button" data-template="environment_sensitivity" data-builder-category="environment">
                  <span>快速模板</span>
                  <strong>环境敏感性</strong>
                  <p>扫描 `system.environment`，适合比较理想零扰动与轨道环境扰动下的误差和扰动力矩预算。</p>
                </button>
                <button class="template-card" type="button" data-template="disturbance_breakdown" data-builder-category="environment">
                  <span>快速模板</span>
                  <strong>环境扰动分解</strong>
                  <p>扫描 `system.disturbance_profile`，适合逐项比较重力梯度、残余磁矩、气动和太阳压的影响。</p>
                </button>
                <button class="template-card" type="button" data-template="disturbance_capability_tradeoff" data-builder-category="actuator">
                  <span>快速模板</span>
                  <strong>扰动-执行器权衡</strong>
                  <p>同时扫描 `system.disturbance_profile` 和轮组最大力矩，适合比较主导扰动与执行器余量的耦合边界。</p>
                </button>
                <button class="template-card" type="button" data-template="sensor_sensitivity" data-builder-category="sensing">
                  <span>快速模板</span>
                  <strong>测量质量敏感性</strong>
                  <p>扫描陀螺噪声，适合比较测量质量变化对闭环误差和通过率的影响。</p>
                </button>
                <button class="template-card" type="button" data-template="acceptance_gate" data-builder-category="acceptance">
                  <span>快速模板</span>
                  <strong>严格验收门限</strong>
                  <p>在同一 PD 基线下使用更严格验收规则，适合识别哪些参数点只是“能跑”，哪些参数点真的稳健。</p>
                </button>
                <button class="template-card" type="button" data-template="momentum_management" data-builder-category="actuator">
                  <span>快速模板</span>
                  <strong>轮速与动量管理</strong>
                  <p>扫描轮组 `momentum_gain`，适合研究轮速管理对姿态保持与执行器余量的影响。</p>
                </button>
              </div>
              <details class="fold-panel" style="margin-top:10px">
                <summary>实验设计画布与协议卡<span>按需展开流程图、协议定位和推荐结果</span></summary>
                <div class="fold-panel-body">
                  <div class="callout info" style="margin-top:10px">
                    <strong>实验设计画布</strong>
                    <p>下面这张画布会把当前研究问题、场景、变量、任务、验收和结果产物串成一条流程，先看结构，再看字段。</p>
                  </div>
                  <div id="builder-visual-canvas" class="flow-sequence"></div>
                  <div class="intro-grid" style="margin-top:10px">
                    <div class="callout" style="margin-bottom:0">
                      <strong>当前场景说明</strong>
                      <p id="builder-selected-scenario">选择场景后，这里会说明它适合做什么实验。</p>
                    </div>
                    <div class="callout" style="margin-bottom:0">
                      <strong>当前实验说明</strong>
                      <p id="builder-selected-experiment">选择扫描变量、任务模板和任务模式后，这里会总结当前实验意图。</p>
                    </div>
                    <div class="callout info" style="margin-bottom:0">
                      <strong>当前协议定位</strong>
                      <p id="builder-selected-protocol">选择模板后，这里会把当前实验放回一条标准研究协议中，而不只是显示零散字段。</p>
                    </div>
                    <div class="callout" style="margin-bottom:0">
                      <strong>推荐步骤与结果</strong>
                      <p id="builder-protocol-workflow">选择模板后，这里会给出推荐步骤、结果产物和下一条实验链路。</p>
                    </div>
                  </div>
                  <div id="builder-protocol-sheet" class="history-detail" style="margin-top:10px">
                    <strong>标准实验协议卡</strong>
                    <p>选择模板后，这里会同步展示推荐变量、验收口径、建议结果图和代表实验入口。</p>
                  </div>
                </div>
              </details>
              <div class="toolbar" style="margin-top:10px">
                <button class="primary" type="button" data-builder-stage-target="variables">下一步：设计变量</button>
              </div>
            </div>
            <div class="builder-stage-panel builder-stage-content" data-builder-stage="variables" hidden>
              <div class="builder-step-title">场景与变量设计</div>
              <div class="callout">
                <strong>这一阶段回答什么</strong>
                <p>明确你要比较的变量、推荐扫描值和 run 规模。平台会把这些配置展开为 sweep 或 Monte Carlo 实验集合。</p>
              </div>
              <div class="form-grid">
                <label>扫描变量模板
                  <select id="builder-sweep-preset">
                    <option value="custom">手动填写</option>
                    <option value="controller.pd_kp">控制器比例增益 pd_kp</option>
                    <option value="controller.pd_kd">控制器微分增益 pd_kd</option>
                    <option value="system.controller">控制器类型 PD/LADRC</option>
                    <option value="system.disturbance_profile">扰动配置模板</option>
                    <option value="sensors.gyro.noise_std_rad_s">陀螺噪声强度</option>
                    <option value="time.seed">随机种子 time.seed</option>
                    <option value="actuators.reaction_wheels.max_torque_nm">轮组最大力矩</option>
                    <option value="actuators.reaction_wheels.momentum_gain">轮组动量管理增益</option>
                  </select>
                  <span class="field-help" id="builder-preset-help">选择常见扫描变量后，平台会自动填入推荐路径和一组可直接试跑的示例取值。</span>
                </label>
                <label>扫描取值模板
                  <select id="builder-sweep-values-preset">
                    <option value="custom">手动填写</option>
                  </select>
                  <span class="field-help" id="builder-values-preset-help">选择模板后会自动填充一组推荐扫描值，你也可以继续手动修改。</span>
                </label>
                <label>扫描取值
                  <input id="builder-sweep-values" placeholder="1.2,1.5">
                  <span class="field-help" id="builder-values-help">参数扫描的候选值列表，用逗号分隔。每个值会生成一组实验分支。</span>
                </label>
              </div>
              <div class="callout" style="margin-top:10px">
                <strong>变量和实验规模预览</strong>
                <p id="builder-preview">当前计划会基于 1 个场景生成 1 个 run。</p>
              </div>
              <div id="builder-variable-brief" class="history-detail" style="margin-top:10px">
                <strong>变量设计任务书</strong>
                <p>这里会把主变量、推荐取值、二维扩展和结果图包压成一张变量设计卡。</p>
              </div>
              <details class="fold-panel" data-builder-view-mode="advanced">
                <summary>高级变量配置<span>二维扫描、底层路径和 Monte Carlo</span></summary>
                <div class="fold-panel-body">
                  <div class="form-grid" style="margin-top:10px">
                    <label>扫描参数路径
                      <input id="builder-sweep-path" value="controller.pd_kp">
                      <span class="field-help" id="builder-sweep-help">要扫描的变量路径，例如 `controller.pd_kp`、`time.seed`、`actuators.reaction_wheels.max_torque_nm`。</span>
                    </label>
                    <label>第二扫描变量模板
                      <select id="builder-second-sweep-preset">
                        <option value="custom">不启用</option>
                        <option value="controller.pd_kp">控制器比例增益 pd_kp</option>
                        <option value="controller.pd_kd">控制器微分增益 pd_kd</option>
                        <option value="system.environment">环境配置 zero/orbital</option>
                        <option value="system.disturbance_profile">扰动配置模板</option>
                        <option value="sensors.gyro.noise_std_rad_s">陀螺噪声强度</option>
                        <option value="actuators.reaction_wheels.max_torque_nm">轮组最大力矩</option>
                        <option value="actuators.reaction_wheels.momentum_gain">轮组动量管理增益</option>
                      </select>
                      <span class="field-help" id="builder-second-preset-help">需要二维实验时启用第二扫描变量。它会和第一变量做笛卡尔组合，生成更完整的权衡实验。</span>
                    </label>
                    <label>第二扫描参数路径
                      <input id="builder-second-sweep-path" placeholder="例如 system.disturbance_profile">
                      <span class="field-help" id="builder-second-sweep-help">第二维扫描的配置路径。常用于环境扰动与执行器能力的双变量实验。</span>
                    </label>
                    <label>第二扫描取值模板
                      <select id="builder-second-sweep-values-preset">
                        <option value="custom">手动填写</option>
                      </select>
                      <span class="field-help" id="builder-second-values-preset-help">选择模板后会自动填充第二维推荐取值，也可以继续手动修改。</span>
                    </label>
                    <label>第二扫描取值
                      <input id="builder-second-sweep-values" placeholder="例如 \"all\",\"aerodynamic_only\"">
                      <span class="field-help" id="builder-second-values-help">第二维参数扫描的候选值列表。和第一维一起决定总 run 数。</span>
                    </label>
                    <label>Monte Carlo 样本数
                      <input id="builder-mc-samples" type="number" min="0" value="0">
                      <span class="field-help">设置为大于 0 时，平台会按不同随机种子重复运行，适合做鲁棒性和统计分析。</span>
                    </label>
                    <label>随机种子
                      <input id="builder-mc-seed" type="number" placeholder="10">
                      <span class="field-help">Monte Carlo 的起始种子。相同计划和种子可复现同一批实验。</span>
                    </label>
                  </div>
                </div>
              </details>
              <div class="toolbar" style="margin-top:10px">
                <button class="secondary" type="button" data-builder-stage-target="question">上一步：研究问题</button>
                <button class="primary" type="button" data-builder-stage-target="runtime">下一步：任务与验收</button>
              </div>
            </div>
            <div class="builder-stage-panel builder-stage-content" data-builder-stage="runtime" hidden>
              <div class="builder-step-title">任务流程与验收标准</div>
              <div class="callout">
                <strong>这一阶段回答什么</strong>
                <p>定义任务模式、参考目标、输出目录和通过标准。它决定实验不只是“跑出来”，而是能不能形成工程上可复查的结论。</p>
              </div>
              <div class="form-grid">
                <label>任务模板
                  <select id="builder-mission"><option value="single_mode">单模式保持</option><option value="detumble_then_hold">消旋后保持</option></select>
                  <span class="field-help" id="builder-mission-help">定义任务流程。单模式保持适合稳态验证，消旋后保持适合先消旋再进入目标模式的演示。</span>
                </label>
                <label>任务模式
                  <select id="builder-mode">
                    <option value="inertial_hold">惯性保持 inertial_hold</option>
                    <option value="sun_pointing">太阳指向 sun_pointing</option>
                    <option value="earth_pointing">对地指向 earth_pointing</option>
                    <option value="safe">安全模式 safe</option>
                  </select>
                  <span class="field-help" id="builder-mode-help">模式名称，例如 `inertial_hold`、`sun_pointing`、`earth_pointing`、`safe`。</span>
                </label>
                <label>验收模板
                  <select id="builder-acceptance-preset">
                    <option value="standard_hold">标准姿态保持</option>
                    <option value="strict_hold">严格闭环验证</option>
                    <option value="transition_demo">模式切换演示</option>
                    <option value="actuator_limited">执行器受限验证</option>
                    <option value="custom">自定义验收</option>
                  </select>
                  <span class="field-help" id="builder-acceptance-help">为实验选择一个结果判断模板。平台会自动填入推荐阈值，并在结果页显示通过/失败统计。</span>
                </label>
              </div>
              <details class="fold-panel" data-builder-view-mode="advanced">
                <summary>高级任务与验收配置<span>参考目标、输出目录和细阈值</span></summary>
                <div class="fold-panel-body">
                  <div class="form-grid" style="margin-top:10px">
                    <label id="builder-detumble-label">消旋时长 s
                      <input id="builder-detumble" type="number" step="0.1" value="0.5">
                      <span class="field-help" id="builder-detumble-help">仅在“消旋后保持”模板中生效，表示 detumble 阶段持续时间。</span>
                    </label>
                    <label>参考目标
                      <select id="builder-reference">
                        <option value="body_zero">机体零姿态 body_zero</option>
                        <option value="sun">太阳参考 sun</option>
                        <option value="nadir">对地参考 nadir</option>
                      </select>
                      <span class="field-help" id="builder-reference-help">姿态参考定义，决定控制器要跟踪的目标姿态。</span>
                    </label>
                    <label>输出目录
                      <input id="builder-output" placeholder="results/platform_ui/quick_pd_sweep">
                      <span class="field-help" id="builder-output-help">实验结果保存位置。留空时会按默认规则生成到 `results/platform_ui/` 下。</span>
                    </label>
                    <label>末端误差阈值 deg
                      <input id="builder-accept-final" type="number" step="0.1" value="40">
                      <span class="field-help">超过这个阈值时，实验会被判定为末端姿态误差不通过。</span>
                    </label>
                    <label>RMS 误差阈值 deg
                      <input id="builder-accept-rms" type="number" step="0.1" value="40">
                      <span class="field-help">用于衡量整个时间区间的整体误差水平，适合筛掉持续振荡或长时间偏差。</span>
                    </label>
                    <label>峰值力矩阈值 Nm
                      <input id="builder-accept-torque" type="number" step="0.01" value="0.2">
                      <span class="field-help">用于限制控制动作强度，适合执行器能力评估和工程可实现性判断。</span>
                    </label>
                  </div>
                </div>
              </details>
              <div id="builder-runtime-brief" class="history-detail" style="margin-top:10px">
                <strong>任务与验收任务书</strong>
                <p>这里会把任务模板、模式、参考目标、验收口径和交付物固定成一条任务链。</p>
              </div>
              <div class="toolbar" style="margin-top:10px">
                <button class="secondary" type="button" data-builder-stage-target="variables">上一步：场景与变量</button>
                <button class="primary" type="button" data-builder-stage-target="review">下一步：预览与生成</button>
              </div>
            </div>
            <div class="builder-stage-panel builder-stage-content" data-builder-stage="review" hidden>
              <div class="builder-step-title">计划预览与生成</div>
              <div class="callout">
                <strong>生成前确认</strong>
                <p>这里集中确认 run 数、输出目录、实验意图和验收模板。确认无误后就可以把它固化为实验计划，或直接创建并运行。</p>
              </div>
              <div class="callout" style="margin-top:10px">
                <strong>变量和实验规模预览</strong>
                <p id="builder-output-preview" style="margin-top:0">输出目录会写入默认实验结果路径。</p>
              </div>
              <div class="summary-grid" id="builder-summary-cards" style="margin-top:10px"></div>
              <div id="builder-review-brief" class="history-detail" style="margin-top:10px">
                <strong>创建后阅读路径</strong>
                <p>这里会把计划创建后该如何进入结果总览、图表、回放和下一协议整理成一条固定路径。</p>
              </div>
              <div class="toolbar" style="margin-top:10px">
                <button class="secondary" type="button" data-builder-stage-target="runtime">上一步：任务与验收</button>
              </div>
              <div class="toolbar" style="margin-top:10px">
                <button class="primary" id="create-plan" type="button">创建实验计划</button>
                <button class="secondary" id="create-plan-run" type="button">创建并运行</button>
              </div>
                <div id="builder-result" class="callout success" style="margin-top:10px" hidden>
                  <strong id="builder-result-title">实验计划已创建</strong>
                  <p id="builder-result-body">这里会显示刚创建好的实验计划、运行规模和后续操作入口。</p>
                <div class="toolbar" style="margin:10px 0 0">
                  <button id="builder-result-open-plan" class="secondary" type="button" disabled>查看新计划</button>
                  <button id="builder-result-run" class="secondary" type="button" disabled>运行这个计划</button>
                  <button id="builder-result-open-result" class="secondary" type="button" disabled>查看结果</button>
                </div>
              </div>
            </div>
            </div>
          </section>
            </div>
            <div class="workspace-view" data-lab-view="manage" hidden>
              <div class="workspace-shell">
                <div class="workspace-nav" id="manage-nav">
                  <button class="active" type="button" data-manage-view="pool">实验池</button>
                  <button type="button" data-manage-view="editor">当前计划</button>
                  <button type="button" data-manage-view="history">版本与归档</button>
                  <button type="button" data-manage-view="workspace">场景与记录</button>
                </div>
                <div class="workspace-summary" id="manage-summary">
                  <div id="manage-summary-copy"><strong>当前工作区：实验池。</strong> 先选实验、看状态，再决定进入编辑、批量校验还是归档。</div>
                </div>
                <div class="workspace-view-stack">
                  <div class="manage-view" data-manage-view="pool">
                    <div class="editor-view-shell" style="margin-bottom:12px">
                      <div class="segment-control" id="manage-pool-toggle">
                        <button class="active" type="button" data-manage-pool-view="focus">计划焦点</button>
                        <button type="button" data-manage-pool-view="roadmap">主线看板</button>
                        <button type="button" data-manage-pool-view="assets">资产主线</button>
                        <button type="button" data-manage-pool-view="plans">计划清单</button>
                      </div>
                      <div class="editor-view-note" id="manage-pool-summary">默认先看计划焦点，再按主线看板和资产主线切换，最后进入计划清单做筛选和批量操作。</div>
                    </div>
                    <div class="workspace-panel-view" data-manage-pool-view="workspace">
                      <div class="workspace-layout">
                        <aside class="workspace-sidecard">
                          <strong>实验选择</strong>
                          <p>先在这里选要继续的实验。计划管理首屏先解决“继续哪个计划”；新计划去“创建实验”，标准模板去“实验库”。</p>
                          <div class="summary-grid" id="manage-side-signals" style="margin-top:12px">
                            <div class="summary-card"><span>焦点计划</span><strong>读取中</strong></div>
                            <div class="summary-card"><span>结果状态</span><strong>读取中</strong></div>
                            <div class="summary-card"><span>主线位置</span><strong>读取中</strong></div>
                            <div class="summary-card"><span>下一动作</span><strong>读取中</strong></div>
                          </div>
                          <div class="workspace-nav workspace-nav-vertical" style="margin-top:12px">
                            <button id="manage-open-library" class="secondary" type="button">打开实验库<span>从标准模板和主线入口开始，而不是从空白 JSON 起步。</span></button>
                            <button id="manage-open-builder" type="button">去创建实验<span>把当前研究问题固化成新的计划副本或下一轮实验。</span></button>
                            <button id="manage-refresh-top" class="secondary" type="button">刷新实验列表<span>重新读取工作区里的场景、计划、结果和归档状态。</span></button>
                            <button class="secondary" type="button" onclick="switchResultsView('overview')">打开结果总览<span>如果当前计划已有结果，直接回结果层继续读图和回放。</span></button>
                          </div>
                          <div class="workspace-nav-note">
                            先锁定焦点计划，再决定是进入编辑、先跑首轮结果，还是回结果层继续读图。
                          </div>
                          <div id="manage-side-route" style="margin-top:12px">
                            <div class="history-detail" style="margin-bottom:0">
                              <strong>主线建议</strong>
                              <p>这里会固定显示当前最适合补的主线实验和展示入口。</p>
                            </div>
                          </div>
                          <div id="experiment-picker" style="margin-top:12px">
                            <div class="history-detail" style="margin-bottom:0">
                              <strong>正在载入实验选择器</strong>
                              <p>刷新后显示最近计划和快速切换入口。</p>
                            </div>
                          </div>
                        </aside>
                        <div class="workspace-content section-stack">
                          <section class="compact-section" id="manage-pool-focus-panel">
                            <h2>计划焦点驾驶舱</h2>
                            <div id="manage-pool-cockpit">
                              <div class="callout">
                                <strong>计划焦点驾驶舱</strong>
                                <p>这里先锁定当前最值得继续推进的计划，再决定是编辑、运行还是回结果层。</p>
                              </div>
                            </div>
                          </section>
                          <section class="compact-section" id="manage-pool-workbench-panel">
                            <h2>计划管理工作台</h2>
                            <div id="manage-workbench-summary">
                              <div class="callout">
                                <strong>计划管理工作台</strong>
                                  <p>这里汇总当前焦点计划和下一步动作。</p>
                              </div>
                            </div>
                          </section>
                        </div>
                      </div>
                    </div>
                    <div class="workspace-panel-view" data-manage-pool-view="plans" hidden>
                      <section>
                        <h2>实验计划池</h2>
                        <div class="toolbar">
                          <button id="refresh" class="secondary">刷新工作区</button>
                          <input id="output" placeholder="可选输出目录，例如 results/platform_ui/demo">
                          <input id="experiment-filter" placeholder="按计划名或场景筛选实验">
                          <select id="experiment-status-filter">
                            <option value="all">全部计划</option>
                            <option value="with_results">已有结果</option>
                            <option value="without_results">暂无结果</option>
                            <option value="current">当前编辑</option>
                          </select>
                        </div>
                        <div id="experiment-list-switcher" style="margin-top:10px">
                          <div class="quick-select-panel" style="margin:0 0 10px">
                            <strong>实验切换</strong>
                            <p>这里会显示当前筛选范围内的实验选择器，便于直接切换到要编辑的计划。</p>
                          </div>
                        </div>
                        <div id="experiment-batch-bar" style="margin-top:10px"></div>
                        <div id="experiments"></div>
                      </section>
                    </div>
                  </div>
                  <div class="manage-view" data-manage-view="editor" hidden>
                    <section id="editor-section">
                      <h2>实验计划编辑器</h2>
                      <div class="editor-meta">
                        <div>
                          <strong id="editor-title">还没有载入实验计划</strong>
                          <div class="subtle" id="editor-path">从实验池选择“载入编辑”。</div>
                        </div>
                        <span class="chip" id="editor-summary">未选择</span>
                      </div>
                      <div class="editor-toolbar">
                        <button id="editor-load" class="secondary" type="button" disabled>重新载入</button>
                        <button id="editor-save" class="primary" type="button" disabled>保存计划</button>
                        <button id="editor-validate" class="secondary" type="button" disabled>保存并校验</button>
                        <button id="editor-run" class="secondary" type="button" disabled>保存并运行</button>
                        <button id="editor-duplicate" class="secondary" type="button" disabled>另存为副本</button>
                        <button id="editor-rename" class="secondary" type="button" disabled>重命名计划</button>
                        <button id="editor-archive" class="secondary" type="button" disabled>归档计划</button>
                      </div>
                      <div class="editor-view-shell">
                        <div class="segment-control" id="editor-workbench-toggle">
                          <button class="active" type="button" data-editor-workbench-view="context">计划画像</button>
                          <button type="button" data-editor-workbench-view="edit">结构编辑</button>
                          <button type="button" data-editor-workbench-view="support">场景与记录</button>
                        </div>
                        <div class="editor-view-note" id="editor-workbench-summary">默认先看计划画像，先确认这份计划在实验主线中的位置、研究问题和下一步动作，再进入字段级编辑。</div>
                      </div>
                      <div class="editor-workbench-panel" data-editor-workbench-view="context">
                        <div id="editor-plan-context" class="callout" style="margin-top:10px">载入实验计划后，这里会显示当前计划在实验平台中的定位、研究问题和推荐下一步。</div>
                        <div id="editor-quick-load">
                          <div class="quick-select-panel">
                            <strong>实验快速切换</strong>
                            <p>工作区刷新后，这里会显示当前可编辑的实验计划。</p>
                          </div>
                        </div>
                      </div>
                      <div class="editor-workbench-panel" data-editor-workbench-view="edit" hidden>
                        <div class="editor-view-shell">
                          <div class="segment-control" id="editor-view-toggle">
                            <button class="active" type="button" data-editor-view="overview">结构概览</button>
                            <button type="button" data-editor-view="json">JSON 编辑</button>
                          </div>
                          <div class="editor-view-note" id="editor-view-summary">默认先显示实验计划概览，只有在需要精确修改字段时再切到 JSON。</div>
                        </div>
                        <div id="editor-overview" class="callout" style="margin-bottom:10px">载入实验计划后，这里会显示结构化概览。</div>
                        <textarea id="editor-text" class="editor-area" spellcheck="false" placeholder="这里会显示实验计划 JSON。" hidden></textarea>
                        <div class="editor-help" id="editor-help">编辑器会按 JSON 保存，并在保存前执行严格校验。相对场景路径会按计划文件所在目录解析。</div>
                      </div>
                      <div class="editor-workbench-panel" data-editor-workbench-view="support" hidden>
                        <div class="grid">
                          <div>
                            <section>
                              <h2>当前场景</h2>
                              <div id="scenario-summary" class="empty">选择一个场景后，这里会显示时间、控制器、环境和输出配置。</div>
                            </section>
                          </div>
                          <div>
                            <section>
                              <h2>操作记录</h2>
                              <div id="activity-feed" class="activity-feed">
                                <div class="empty">平台的查看、校验、创建和运行操作会记录在这里。</div>
                              </div>
                            </section>
                          </div>
                        </div>
                      </div>
                    </section>
                  </div>
                  <div class="manage-view" data-manage-view="history" hidden>
                    <div class="grid">
                      <div>
                        <section>
                          <h2>最近实验计划</h2>
                          <div id="recent-experiment-plans"></div>
                        </section>
                      </div>
                      <div>
                        <section>
                          <h2>已归档计划</h2>
                          <div id="archived-experiment-plans"></div>
                        </section>
                      </div>
                    </div>
                  </div>
                  <div class="manage-view" data-manage-view="workspace" hidden>
                    <div class="grid">
                      <div>
                        <section>
                          <h2>场景</h2>
                          <div id="scenarios"></div>
                        </section>
                      </div>
                      <div>
                        <section>
                          <h2>当前场景摘要</h2>
                          <div id="scenario-summary-workspace" class="empty">选择或校验场景后，这里会显示当前场景摘要副本，便于做计划前复查。</div>
                        </section>
                      </div>
                    </div>
                    <section>
                      <h2>操作记录</h2>
                      <div id="activity-feed-workspace" class="activity-feed">
                        <div class="empty">平台的查看、校验、创建和运行操作会记录在这里。</div>
                      </div>
                    </section>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
    <div class="page-view" data-page-view="results" hidden>
      <div class="workspace-shell">
        <div class="workspace-nav" id="results-nav">
          <button class="active" type="button" data-results-view="overview">结果总览</button>
          <button type="button" data-results-view="compare">结果对比</button>
          <button type="button" data-results-view="replay">姿态回放</button>
          <button type="button" data-results-view="preview">Dashboard 预览</button>
        </div>
        <div class="workspace-summary" id="results-summary">
          <div id="results-summary-copy"><strong>当前工作台：结果总览。</strong> 先看这次实验整体是否通过、最佳 run 是谁、当前运行状态如何，再进入对比、回放和 dashboard。</div>
        </div>
        <div class="workspace-view" data-results-view="overview">
          <div class="editor-view-shell" style="margin-bottom:12px">
            <div class="segment-control" id="results-overview-toggle">
              <button class="active" type="button" data-results-overview-view="cockpit">驾驶舱</button>
              <button type="button" data-results-overview-view="browser">结果列表</button>
              <button type="button" data-results-overview-view="focus">当前结果</button>
            </div>
            <div class="editor-view-note" id="results-overview-summary">有结果时优先进入当前结果；没有焦点结果时回到结果列表或驾驶舱。</div>
          </div>
          <div class="workspace-panel-view" data-results-overview-view="cockpit">
            <div class="section-stack">
              <div class="workbench-hero-grid">
                <section class="compact-section">
                  <h2>结果驾驶舱</h2>
                  <div id="results-overview-cockpit">
                    <div class="callout">
                      <strong>正在整理结果驾驶舱</strong>
                      <p>这里汇总当前焦点结果、风险结果和下一步动作。</p>
                    </div>
                  </div>
                </section>
                <section class="compact-section">
                  <h2>结果入口板</h2>
                  <div id="results-quickstart-panel">
                    <div class="callout">
                      <strong>正在整理结果入口</strong>
                      <p>这里固定显示焦点结果、风险出口和下一跳，方便快速进入当前最值得读的一项结果。</p>
                    </div>
                  </div>
                </section>
              </div>
            </div>
          </div>
          <div class="workspace-panel-view" data-results-overview-view="browser" hidden>
            <section>
              <h2>结果浏览</h2>
              <div class="toolbar">
                <input id="dashboard-filter" placeholder="按实验名或场景筛选结果">
                <select id="dashboard-status-filter">
                  <option value="all">全部结果</option>
                  <option value="latest">仅最新运行</option>
                  <option value="current">仅当前查看</option>
                  <option value="accepted">通过率 100%</option>
                  <option value="needs_attention">存在失败</option>
                </select>
                <select id="dashboard-line-filter">
                  <option value="all">全部主线</option>
                </select>
              </div>
              <div id="latest-dashboard-banner"></div>
              <div class="editor-view-shell" style="margin:12px 0">
                <div class="segment-control" id="results-browser-toggle">
                  <button class="active" type="button" data-results-browser-view="representative">代表结果</button>
                  <button type="button" data-results-browser-view="map">结果地图</button>
                  <button type="button" data-results-browser-view="risk">风险结果</button>
                  <button type="button" data-results-browser-view="recent">最近结果</button>
                </div>
                <div class="editor-view-note" id="results-browser-summary">默认先看代表结果，先锁定各条主线最适合继续阅读的入口，再进入完整结果表。</div>
              </div>
              <div id="results-browser-cockpit"></div>
              <div id="dashboards"></div>
            </section>
          </div>
          <div class="workspace-panel-view" data-results-overview-view="focus" hidden>
            <div class="grid">
              <div>
                <section id="result-section">
                  <h2>当前结果</h2>
                  <div id="result-summary" class="empty">运行实验或选择一个结果目录后，这里会显示实验摘要、最佳 run 和关键文件。</div>
                </section>
              </div>
              <div>
                <section id="run-status-section">
                  <h2>运行状态</h2>
                  <div class="run-status-stack">
                    <div id="run-progress-panel" class="empty">点击“运行计划”或“创建并运行”后，这里会显示当前执行状态。</div>
                    <div id="latest-run-summary-panel" class="empty">最近一次运行完成后，这里会固定显示结果摘要和快捷入口。</div>
                  </div>
                </section>
              </div>
            </div>
          </div>
        </div>
        <div class="workspace-view" data-results-view="compare" hidden>
          <div class="section-stack">
            <div class="workbench-hero-grid">
              <section class="compact-section">
                <h2>关键 Run 对比</h2>
                <div class="callout">
                  <strong>对比建议</strong>
                  <p>先从结果总览里锁定最佳 run、最差 run 或重点工况，再在这里比较指标、参数差异和误差/力矩曲线。</p>
                </div>
                <div id="compare-view" class="empty">选择结果目录后，这里会显示最佳/最差或关键 run 的对照指标和曲线。</div>
              </section>
              <section class="compact-section">
                <h2>对比入口板</h2>
                <div id="compare-quickstart-panel">
                  <div class="callout">
                    <strong>正在整理对比入口</strong>
                    <p>这里固定显示当前对比组合、推荐回放对象和回主线入口。</p>
                  </div>
                </div>
              </section>
            </div>
          </div>
        </div>
        <div class="workspace-view" data-results-view="replay" hidden>
          <div class="section-stack">
            <div class="workbench-hero-grid">
              <section class="compact-section">
                <h2>三维姿态回放</h2>
                <div class="callout">
                  <strong>回放建议</strong>
                  <p>这里适合看模式切换、姿态误差演化和 runtime 快照联动，尤其适合做演示和复盘。</p>
                </div>
                <div id="replay-view" class="empty">选择结果目录后，这里会显示基于真实四元数的姿态回放。</div>
              </section>
              <section class="compact-section">
                <h2>回放入口板</h2>
                <div id="replay-quickstart-panel">
                  <div class="callout">
                    <strong>正在整理回放入口</strong>
                    <p>这里固定显示当前回放对象、任务模式入口和调度快照入口。</p>
                  </div>
                </div>
              </section>
            </div>
          </div>
        </div>
        <div class="workspace-view" data-results-view="preview" hidden>
          <section>
            <h2>结果预览</h2>
            <div class="callout">
              <strong>静态结果界面</strong>
              <p>这里会直接预览实验目录里的 `dashboard.html`。它是可脱离平台单独打开的结果界面，适合归档、分享和汇报。</p>
            </div>
            <div id="preview-shell" class="preview-shell">
              <div class="preview-meta">
                <span class="subtle" id="preview-title">还没有加载结果界面</span>
                <div class="toolbar" style="margin:0">
                  <button id="open-dashboard" class="secondary" type="button" disabled>新窗口打开</button>
                </div>
              </div>
              <div id="preview-workbench" style="padding:12px" hidden></div>
              <div id="preview-empty" class="empty" style="margin:14px">选择结果界面后，会在这里直接预览带动画和仿真结果图的 dashboard。</div>
              <iframe id="preview-frame" title="dashboard preview" hidden></iframe>
            </div>
          </section>
        </div>
      </div>
    </div>
  </main>
  </div>
  </div>
  <script>
    (() => {
      const reportBootError = (message) => {
        const statusNode = document.getElementById('status');
        if (statusNode) {
          statusNode.className = 'status bad';
          statusNode.textContent = `前端初始化失败：${message}`;
        }
      };
      window.addEventListener('error', event => {
        const message = event?.error?.message || event?.message || 'unknown error';
        reportBootError(message);
      });
      window.addEventListener('unhandledrejection', event => {
        const reason = event?.reason;
        const message = typeof reason === 'string'
          ? reason
          : reason?.message || 'promise rejected';
        reportBootError(message);
      });
      const fallbackSummaryText = {
        overview: '<strong>当前视图：总览。</strong> 先看平台定位、实验范围和最近进展，再进入实验设计与结果工作台。',
        lab: '<strong>当前视图：实验设计。</strong> 通过左侧菜单在实验库、创建实验和计划管理之间切换。',
        results: '<strong>当前视图：运行结果。</strong> 通过左侧菜单在结果总览、对比、回放和 Dashboard 预览之间切换。',
      };
      const activateButtons = (selector, predicate) => {
        document.querySelectorAll(selector).forEach(button => {
          button.classList.toggle('active', Boolean(predicate(button)));
        });
      };
      const showNodes = (selector, activeValue, key) => {
        document.querySelectorAll(selector).forEach(node => {
          node.hidden = node.dataset[key] !== activeValue;
        });
      };
      const fallbackRouteParamsFor = (page, detail = {}) => {
        const params = new URLSearchParams();
        params.set('page', page || 'overview');
        if (detail.labView) params.set('lab', detail.labView);
        if (detail.manageView) params.set('manage', detail.manageView);
        if (detail.resultsView) params.set('results', detail.resultsView);
        return params.toString();
      };
      const fallbackRouteRaw = () => {
        const hash = String(window.location.hash || '').replace(/^#/, '').trim();
        if (hash) return hash;
        return String(window.location.search || '').replace(/^\\?/, '').trim();
      };
      const fallbackRouteUrlFor = (raw) => {
        const params = String(raw || '').replace(/^[#?]/, '').trim();
        if (!params) return window.location.pathname;
        return `${window.location.pathname}?${params}#${params}`;
      };
      const fallbackRouteFromHash = () => {
        const raw = fallbackRouteRaw();
        const params = new URLSearchParams(raw);
        const page = params.get('page') || 'overview';
        return {
          page,
          labView: params.get('lab') || params.get('labView') || undefined,
          manageView: params.get('manage') || params.get('manageView') || undefined,
          resultsView: params.get('results') || params.get('resultsView') || undefined,
        };
      };
      const fallbackNavigate = (page, detail = {}, syncHash = true) => {
        const currentPage = page || 'overview';
        showNodes('[data-page-view]', currentPage, 'pageView');
        activateButtons('#sidebar-nav [data-page]', button => {
          const buttonPage = button.dataset.page || 'overview';
          if (buttonPage !== currentPage) return false;
          if (buttonPage === 'lab' && detail.labView) {
            return (button.dataset.labView || 'library') === detail.labView;
          }
          if (buttonPage === 'results' && detail.resultsView) {
            return (button.dataset.resultsView || 'overview') === detail.resultsView;
          }
          return true;
        });
        activateButtons('#page-nav [data-page]', button => (button.dataset.page || 'overview') === currentPage);
        if (currentPage === 'lab') {
          const labView = detail.labView || 'library';
          showNodes('.workspace-view[data-lab-view]', labView, 'labView');
          activateButtons('#lab-nav [data-lab-view]', button => (button.dataset.labView || 'library') === labView);
          if (labView === 'manage') {
            const manageView = detail.manageView || 'pool';
            showNodes('.manage-view[data-manage-view]', manageView, 'manageView');
            activateButtons('#manage-nav [data-manage-view]', button => (button.dataset.manageView || 'pool') === manageView);
          }
        }
        if (currentPage === 'results') {
          const resultsView = detail.resultsView || 'overview';
          showNodes('.workspace-view[data-results-view]', resultsView, 'resultsView');
          activateButtons('#results-nav [data-results-view]', button => (button.dataset.resultsView || 'overview') === resultsView);
        }
        const summaryNode = document.getElementById('page-summary-copy');
        if (summaryNode) {
          summaryNode.innerHTML = fallbackSummaryText[currentPage] || fallbackSummaryText.overview;
        }
        if (syncHash) {
          const nextRaw = fallbackRouteParamsFor(currentPage, detail);
          const nextUrl = fallbackRouteUrlFor(nextRaw);
          const currentUrl = `${window.location.pathname}${window.location.search}${window.location.hash}`;
          if (currentUrl !== nextUrl) {
            history.replaceState(null, '', nextUrl);
          }
        }
      };
      const initFallbackNav = () => {
        if (window.__satmodelFallbackNavReady) return;
        window.__satmodelFallbackNavReady = true;
        const bindNavContainer = root => {
          root?.addEventListener('pointerup', event => {
            const button = event.target.closest('[data-page]');
            if (!button || !root.contains(button)) return;
            if (button.tagName === 'A') return;
            event.preventDefault();
            fallbackNavigate(button.dataset.page || 'overview', {
              labView: button.dataset.labView,
              manageView: button.dataset.manageView,
              resultsView: button.dataset.resultsView,
            });
          });
        };
        bindNavContainer(document.getElementById('sidebar-nav'));
        bindNavContainer(document.getElementById('page-nav'));
        document.querySelectorAll('#sidebar-nav [data-page], #page-nav [data-page]').forEach(button => {
          if (button.tagName === 'A') return;
          button.addEventListener('click', event => {
            event.preventDefault();
            fallbackNavigate(button.dataset.page || 'overview', {
              labView: button.dataset.labView,
              manageView: button.dataset.manageView,
              resultsView: button.dataset.resultsView,
            });
          });
        });
        document.querySelectorAll('#lab-nav [data-lab-view]').forEach(button => {
          button.addEventListener('click', event => {
            event.preventDefault();
            fallbackNavigate('lab', {labView: button.dataset.labView || 'library'});
          });
        });
        document.querySelectorAll('#manage-nav [data-manage-view]').forEach(button => {
          button.addEventListener('click', event => {
            event.preventDefault();
            fallbackNavigate('lab', {labView: 'manage', manageView: button.dataset.manageView || 'pool'});
          });
        });
        document.querySelectorAll('#results-nav [data-results-view]').forEach(button => {
          button.addEventListener('click', event => {
            event.preventDefault();
            fallbackNavigate('results', {resultsView: button.dataset.resultsView || 'overview'});
          });
        });
        window.addEventListener('hashchange', () => {
          const route = fallbackRouteFromHash();
          fallbackNavigate(route.page, route, false);
        });
        const route = fallbackRouteFromHash();
        fallbackNavigate(route.page, route, false);
        window.__satmodelFallbackNavigate = fallbackNavigate;
        window.__satmodelDirectNav = (page, detail = {}) => {
          fallbackNavigate(page, detail);
          return false;
        };
      };
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initFallbackNav, {once: true});
      } else {
        initFallbackNav();
      }
    })();
  </script>
  <script>
    (() => {
      try {
    const state = {
      workspace: null,
      currentDashboard: null,
      currentDashboardUrl: null,
      previewViewMode: 'brief',
      latestRunDashboard: null,
      dashboardFilter: '',
      dashboardStatusFilter: 'all',
      dashboardLineFilter: 'all',
      experimentFilter: '',
      experimentStatusFilter: 'all',
      currentPage: 'overview',
      labView: 'library',
      libraryView: 'route',
      manageView: 'pool',
      managePoolView: '',
      manageCockpitView: 'focus',
      manageWorkbenchView: 'focus',
      resultsView: 'overview',
      resultsOverviewView: '',
      resultsCockpitView: 'focus',
      resultsBrowserView: 'representative',
      overviewWorkbenchView: 'position',
      currentScenario: null,
      currentExperiment: null,
      currentExperimentDirty: false,
      currentExperimentSummary: '',
      currentExperimentMapping: null,
      editorQuickPickPath: '',
      editorWorkbenchView: 'context',
      editorViewMode: 'overview',
      editorContextView: 'profile',
      currentDashboardData: null,
      resultSummaryView: 'overview',
      resultWorkbenchView: 'focus',
      resultDetailView: 'summary',
      compareViewMode: 'brief',
      compareWorkbenchView: 'focus',
      compareSelection: {A: null, B: null},
      replayViewMode: 'animation',
      replayWorkbenchView: 'focus',
      replayRun: null,
      runDetailRunId: null,
      runAction: null,
      latestRunSummary: null,
      builderLastCreated: null,
      builderAction: null,
      builderStage: 'question',
      builderWorkbenchView: 'focus',
      builderViewMode: 'basic',
      builderError: '',
      builderCategory: 'all',
      quickDemoSession: null,
      libraryCategory: 'all',
      libraryExperimentId: '',
      libraryDetailView: 'protocol',
      selectedExperiments: [],
      activity: [],
    };
    const status = document.getElementById('status');
    const output = document.getElementById('output');
    const experimentFilter = document.getElementById('experiment-filter');
    const experimentStatusFilter = document.getElementById('experiment-status-filter');
    const experimentBatchBar = document.getElementById('experiment-batch-bar');
    const sidebarNav = document.getElementById('sidebar-nav');
    const pageNav = document.getElementById('page-nav');
    const pageSummary = document.getElementById('page-summary');
    const pageSummaryCopy = document.getElementById('page-summary-copy');
    const pageViews = Array.from(document.querySelectorAll('[data-page-view]'));
    const labNav = document.getElementById('lab-nav');
    const labSummaryCopy = document.getElementById('lab-summary-copy');
    const labNavNote = document.getElementById('lab-nav-note');
    const labViews = Array.from(document.querySelectorAll('.workspace-view[data-lab-view]'));
    const libraryWorkspaceToggle = document.getElementById('library-workspace-toggle');
    const libraryWorkspaceSummary = document.getElementById('library-workspace-summary');
    const libraryWorkspaceViews = Array.from(document.querySelectorAll('.workspace-panel-view[data-library-view]'));
    const libraryProtocolMap = document.getElementById('library-protocol-map');
    const manageNav = document.getElementById('manage-nav');
    const manageSummaryCopy = document.getElementById('manage-summary-copy');
    const manageViews = Array.from(document.querySelectorAll('.manage-view[data-manage-view]'));
    const managePoolToggle = document.getElementById('manage-pool-toggle');
    const managePoolSummary = document.getElementById('manage-pool-summary');
    const managePoolViews = Array.from(document.querySelectorAll('.workspace-panel-view[data-manage-pool-view]'));
    const managePoolCockpit = document.getElementById('manage-pool-cockpit');
    const managePoolFocusPanel = document.getElementById('manage-pool-focus-panel');
    const managePoolWorkbenchPanel = document.getElementById('manage-pool-workbench-panel');
    const manageSideSignals = document.getElementById('manage-side-signals');
    const manageSideRoute = document.getElementById('manage-side-route');
    const resultsNav = document.getElementById('results-nav');
    const resultsSummaryCopy = document.getElementById('results-summary-copy');
    const resultsViews = Array.from(document.querySelectorAll('.workspace-view[data-results-view]'));
    const resultsOverviewToggle = document.getElementById('results-overview-toggle');
    const resultsOverviewSummary = document.getElementById('results-overview-summary');
    const resultsOverviewViews = Array.from(document.querySelectorAll('.workspace-panel-view[data-results-overview-view]'));
    const resultsOverviewCockpit = document.getElementById('results-overview-cockpit');
    const resultsQuickstartPanel = document.getElementById('results-quickstart-panel');
    const previewFrame = document.getElementById('preview-frame');
    const previewEmpty = document.getElementById('preview-empty');
    const previewTitle = document.getElementById('preview-title');
    const previewWorkbench = document.getElementById('preview-workbench');
    const openDashboard = document.getElementById('open-dashboard');
    const dashboardFilter = document.getElementById('dashboard-filter');
    const dashboardStatusFilter = document.getElementById('dashboard-status-filter');
    const dashboardLineFilter = document.getElementById('dashboard-line-filter');
    const latestDashboardBanner = document.getElementById('latest-dashboard-banner');
    const resultsBrowserToggle = document.getElementById('results-browser-toggle');
    const resultsBrowserSummary = document.getElementById('results-browser-summary');
    const resultsBrowserCockpit = document.getElementById('results-browser-cockpit');
    const activityFeed = document.getElementById('activity-feed');
    const compareView = document.getElementById('compare-view');
    const compareQuickstartPanel = document.getElementById('compare-quickstart-panel');
    const replayView = document.getElementById('replay-view');
    const replayQuickstartPanel = document.getElementById('replay-quickstart-panel');
    const overviewCards = document.getElementById('overview-cards');
    const overviewWorkbenchToggle = document.getElementById('overview-workbench-toggle');
    const overviewWorkbenchSummary = document.getElementById('overview-workbench-summary');
    const overviewWorkbenchViews = Array.from(document.querySelectorAll('.workspace-panel-view[data-overview-workbench-view]'));
    const overviewWorkbench = document.getElementById('overview-workbench');
    const overviewPositionSignals = document.getElementById('overview-position-signals');
    const overviewPriorityRoute = document.getElementById('overview-priority-route');
    const overviewLatestPlan = document.getElementById('overview-latest-plan');
    const overviewLatestResult = document.getElementById('overview-latest-result');
    const overviewAttention = document.getElementById('overview-attention');
    const overviewPlatformMap = document.getElementById('overview-platform-map');
    const overviewArchitectureMap = document.getElementById('overview-architecture-map');
    const overviewExperimentMap = document.getElementById('overview-experiment-map');
    const overviewStandardRoute = document.getElementById('overview-standard-route');
    const editorTitle = document.getElementById('editor-title');
    const editorPath = document.getElementById('editor-path');
    const editorSummary = document.getElementById('editor-summary');
    const editorWorkbenchToggle = document.getElementById('editor-workbench-toggle');
    const editorWorkbenchSummary = document.getElementById('editor-workbench-summary');
    const editorWorkbenchPanels = Array.from(document.querySelectorAll('[data-editor-workbench-view]'));
    const editorViewToggle = document.getElementById('editor-view-toggle');
    const editorViewSummary = document.getElementById('editor-view-summary');
    const editorOverview = document.getElementById('editor-overview');
    const editorText = document.getElementById('editor-text');
    const editorHelp = document.getElementById('editor-help');
    const editorLoad = document.getElementById('editor-load');
    const editorSave = document.getElementById('editor-save');
    const editorValidate = document.getElementById('editor-validate');
    const editorRun = document.getElementById('editor-run');
    const editorDuplicate = document.getElementById('editor-duplicate');
    const editorRename = document.getElementById('editor-rename');
    const editorArchive = document.getElementById('editor-archive');
    const builderScenario = document.getElementById('builder-scenario');
    const builderScenarioHelp = document.getElementById('builder-scenario-help');
    const builderViewToggle = document.getElementById('builder-view-toggle');
    const builderViewSummary = document.getElementById('builder-view-summary');
    const builderCategoryNav = document.getElementById('builder-category-nav');
    const builderCategoryCopy = document.getElementById('builder-category-copy');
    const builderStageNav = document.getElementById('builder-stage-nav');
    const builderStageSummary = document.getElementById('builder-stage-summary');
    const builderWorkbenchToggle = document.getElementById('builder-workbench-toggle');
    const builderWorkbenchSummaryCopy = document.getElementById('builder-workbench-summary-copy');
    const builderWorkbenchSummary = document.getElementById('builder-workbench-summary');
    const builderStagePanels = Array.from(document.querySelectorAll('[data-builder-stage]'));
    const builderName = document.getElementById('builder-name');
    const builderDescription = document.getElementById('builder-description');
    const builderOutput = document.getElementById('builder-output');
    const builderOutputHelp = document.getElementById('builder-output-help');
    const builderSweepPreset = document.getElementById('builder-sweep-preset');
    const builderPresetHelp = document.getElementById('builder-preset-help');
    const builderSweepPath = document.getElementById('builder-sweep-path');
    const builderSweepValuesPreset = document.getElementById('builder-sweep-values-preset');
    const builderValuesPresetHelp = document.getElementById('builder-values-preset-help');
    const builderSweepValues = document.getElementById('builder-sweep-values');
    const builderValuesHelp = document.getElementById('builder-values-help');
    const builderSecondSweepPreset = document.getElementById('builder-second-sweep-preset');
    const builderSecondPresetHelp = document.getElementById('builder-second-preset-help');
    const builderSecondSweepPath = document.getElementById('builder-second-sweep-path');
    const builderSecondSweepHelp = document.getElementById('builder-second-sweep-help');
    const builderSecondSweepValuesPreset = document.getElementById('builder-second-sweep-values-preset');
    const builderSecondValuesPresetHelp = document.getElementById('builder-second-values-preset-help');
    const builderSecondSweepValues = document.getElementById('builder-second-sweep-values');
    const builderSecondValuesHelp = document.getElementById('builder-second-values-help');
    const builderMcSamples = document.getElementById('builder-mc-samples');
    const builderMcSeed = document.getElementById('builder-mc-seed');
    const builderMission = document.getElementById('builder-mission');
    const builderMode = document.getElementById('builder-mode');
    const builderDetumble = document.getElementById('builder-detumble');
    const builderReference = document.getElementById('builder-reference');
    const builderAcceptancePreset = document.getElementById('builder-acceptance-preset');
    const builderAcceptanceHelp = document.getElementById('builder-acceptance-help');
    const builderAcceptFinal = document.getElementById('builder-accept-final');
    const builderAcceptRms = document.getElementById('builder-accept-rms');
    const builderAcceptTorque = document.getElementById('builder-accept-torque');
    const builderReferenceHelp = document.getElementById('builder-reference-help');
    const builderMissionHelp = document.getElementById('builder-mission-help');
    const builderModeHelp = document.getElementById('builder-mode-help');
    const builderDetumbleLabel = document.getElementById('builder-detumble-label');
    const builderSweepHelp = document.getElementById('builder-sweep-help');
    const builderPreview = document.getElementById('builder-preview');
    const builderOutputPreview = document.getElementById('builder-output-preview');
    const builderSummaryCards = document.getElementById('builder-summary-cards');
    const builderVariableBrief = document.getElementById('builder-variable-brief');
    const builderRuntimeBrief = document.getElementById('builder-runtime-brief');
    const builderReviewBrief = document.getElementById('builder-review-brief');
    const builderSelectedScenario = document.getElementById('builder-selected-scenario');
    const builderSelectedExperiment = document.getElementById('builder-selected-experiment');
    const builderSelectedProtocol = document.getElementById('builder-selected-protocol');
    const builderProtocolWorkflow = document.getElementById('builder-protocol-workflow');
    const builderProtocolSheet = document.getElementById('builder-protocol-sheet');
    const builderVisualCanvas = document.getElementById('builder-visual-canvas');
    const builderTemplateGrid = document.getElementById('builder-template-grid');
    const builderResult = document.getElementById('builder-result');
    const builderResultTitle = document.getElementById('builder-result-title');
    const builderResultBody = document.getElementById('builder-result-body');
    const builderResultOpenPlan = document.getElementById('builder-result-open-plan');
    const builderResultRun = document.getElementById('builder-result-run');
    const builderResultOpenResult = document.getElementById('builder-result-open-result');
    const recentExperimentPlans = document.getElementById('recent-experiment-plans');
    const archivedExperimentPlans = document.getElementById('archived-experiment-plans');
    const libraryStandardRoute = document.getElementById('library-standard-route');
    const quickDemoGrid = document.getElementById('quick-demo-grid');
    const quickDemoStatus = document.getElementById('quick-demo-status');
    const experimentLibraryGrid = document.getElementById('experiment-library-grid');
    const resultSection = document.getElementById('result-section');
    const runProgressPanel = document.getElementById('run-progress-panel');
    const latestRunSummaryPanel = document.getElementById('latest-run-summary-panel');
    const builderSection = document.getElementById('builder-section');
    const editorSection = document.getElementById('editor-section');
    const editorPlanContext = document.getElementById('editor-plan-context');
    const scenarioSummaryWorkspace = document.getElementById('scenario-summary-workspace');
    const experimentPicker = document.getElementById('experiment-picker');
    const manageWorkbenchSummary = document.getElementById('manage-workbench-summary');
    const experimentListSwitcher = document.getElementById('experiment-list-switcher');
    const editorQuickLoad = document.getElementById('editor-quick-load');
    const manageOpenLibraryButton = document.getElementById('manage-open-library');
    const manageOpenBuilderButton = document.getElementById('manage-open-builder');
    const manageRefreshTopButton = document.getElementById('manage-refresh-top');
    const activityFeedWorkspace = document.getElementById('activity-feed-workspace');
    const createPlanButton = document.getElementById('create-plan');
    const createPlanRunButton = document.getElementById('create-plan-run');
    let runProgressTimer = null;
    const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    const fmt = value => value === null || value === undefined || value === '' ? '—' : Number.isFinite(Number(value)) ? Number(value).toPrecision(5).replace(/\\.0+$/, '') : String(value);
    const percentText = value => `${Math.round(Number(value || 0) * 1000) / 10}%`;

    async function api(path, options = {}) {
      const res = await fetch(path, {
        ...options,
        headers: {'Content-Type': 'application/json', ...(options.headers || {})}
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'request failed');
      return data;
    }

    function setStatus(text, cls = '') {
      status.className = `status ${cls}`;
      status.textContent = text;
    }

    function pageSummaryText(page) {
      if (page === 'lab') {
        return '<strong>当前视图：实验设计。</strong> 通过左侧菜单在实验库、创建实验和计划管理之间切换，把实验本身打磨成可复现资产。';
      }
      if (page === 'results') {
        return '<strong>当前视图：运行结果。</strong> 通过左侧菜单在结果总览、对比、回放和 dashboard 预览之间切换。';
      }
      return '<strong>当前视图：总览。</strong> 先看平台定位、实验范围和最近进展，再进入实验设计与结果工作台。';
    }

    function labSummaryText(view) {
      if (view === 'builder') {
        return '<strong>当前工作台：创建实验。</strong> 在这里配置场景、扫描变量、任务模板、Monte Carlo 和验收门限，把实验问题组织成一个清晰计划。';
      }
      if (view === 'manage') {
        return '<strong>当前工作台：计划管理。</strong> 在这里浏览场景、编辑计划、做批量校验和归档，让实验版本保持整洁可追踪。';
      }
      return '<strong>当前工作台：实验库。</strong> 先选一个成熟实验范式，看它在回答什么问题、关注什么指标，再进入创建或管理。';
    }

    function labNavNoteText(view) {
      if (view === 'builder') {
        return '现在已经进入“创建实验”。先在第 1 步里选择实验主线和模板，下面才会出现对应的变量与任务建议。';
      }
      if (view === 'manage') {
        return '现在已经进入“计划管理”。这里负责编辑已有计划；如果想先挑实验类型，请点左侧“实验库”或“创建实验”。';
      }
      return '现在显示的是“实验库”。这里最适合先看有哪些成熟实验，再决定直接复用、载入到创建器，还是回到计划管理继续改已有实验。';
    }

    function manageSummaryText(view) {
      if (view === 'editor') {
        return '<strong>当前工作区：当前计划。</strong> 把结构概览、JSON 编辑、校验、运行和复制操作集中到一处，专注处理正在推进的那一个计划。';
      }
      if (view === 'history') {
        return '<strong>当前工作区：版本与归档。</strong> 在这里看最近计划、派生副本和已归档版本，保持实验版本链条清楚可追溯。';
      }
      if (view === 'workspace') {
        return '<strong>当前工作区：场景与记录。</strong> 这里负责复查场景基线和近期操作记录，适合在真正编辑计划前确认上下文。';
      }
      return '<strong>当前工作区：实验池。</strong> 先选实验、看状态，再决定进入编辑、批量校验还是归档。';
    }

    function managePoolSummaryText(view) {
      if (view === 'roadmap') {
        return '现在显示主线看板。这里按协议阶段检查当前工作区的计划覆盖、代表计划和代表结果。';
      }
      if (view === 'assets') {
        return '现在显示资产主线。这里固定判断代表计划、代表结果和同族副本是否需要收敛。';
      }
      if (view === 'plans') {
        return '现在显示计划清单。这里直接做筛选、载入、运行和归档。';
      }
      return '现在显示计划焦点。这里先锁定当前最值得继续推进的计划，再决定是否回编辑器或结果层。';
    }

    function libraryWorkspaceSummaryText(view) {
      if (view === 'map') {
        return '现在显示实验地图。这里按协议阶段集中看当前有哪些实验、各自回答什么问题，以及代表资产是否已经形成。';
      }
      if (view === 'demo') {
        return '现在显示示例入口。这里适合第一次演示平台时直接跑出结果、动画和 dashboard。';
      }
      if (view === 'template') {
        return '现在显示模板库。这里适合按实验协议、代表资产和学术说明挑一个具体实验继续做。';
      }
      return '默认先看实验主线。先确认平台当前推荐的实验路径，再决定进入示例还是具体模板。';
    }

    function manageCockpitSummaryText(view) {
      if (view === 'health') {
        return '现在显示资产健康。这里看生命周期、主线覆盖和结果沉淀情况。';
      }
      if (view === 'attention') {
        return '现在显示待处理计划。这里收拢待运行、异常和需要收尾的计划。';
      }
      return '默认先看焦点计划。这里先锁定当前最值得推进的计划。';
    }

    function manageWorkbenchSummaryText(view) {
      if (view === 'roadmap') {
        return '现在显示主线看板。这里按协议阶段检查当前工作区的计划覆盖、代表计划和代表结果。';
      }
      if (view === 'assets') {
        return '现在显示资产主线。这里固定看代表计划、代表结果和同族副本是否需要收敛。';
      }
      if (view === 'actions') {
        return '现在显示下一步动作。这里直接决定是编辑、运行、归档还是回结果。';
      }
      return '默认先看计划焦点。这里先锁定当前计划、结果状态和最短推进路径。';
    }

    function editorContextViewNote(view) {
      if (view === 'brief') {
        return '现在显示实验说明卡。这里固定回答研究问题、场景基线、变量、任务、验收、结果图和下一步。';
      }
      if (view === 'roadmap') {
        return '现在显示主线导航。这里看当前计划位于哪一环、代表资产是否形成、下一组实验该接哪里。';
      }
      return '默认先看计划画像。这里先锁定当前计划、变量规模、验收口径和结果联动状态。';
    }

    function builderWorkbenchSummaryText(view) {
      if (view === 'scale') {
        return '现在显示实验规模。这里固定看扫描维度、Monte Carlo 规模、任务模板和验收门限，先确认 run 规模是否合理。';
      }
      if (view === 'deliverables') {
        return '现在显示结果出口。这里固定看输出目录、预期产物、读图顺序和创建后的下一步，保证实验从一开始就是可归档资产。';
      }
      return '默认先看设计焦点，先确认当前研究问题、协议阶段和下一步动作，再进入字段级配置。';
    }

    function resultsCockpitSummaryText(view) {
      if (view === 'risk') {
        return '现在显示风险结果。这里先看失败结果和诊断入口。';
      }
      if (view === 'route') {
        return '现在显示主线导航。这里看代表结果、主线分组和阅读路线。';
      }
      return '默认先看焦点结果。这里先锁定当前最值得读的结果。';
    }

    function resultsSummaryText(view) {
      if (view === 'compare') {
        return '<strong>当前工作台：结果对比。</strong> 重点比较关键 run 的指标、参数差异和误差/力矩曲线。';
      }
      if (view === 'replay') {
        return '<strong>当前工作台：姿态回放。</strong> 这里适合观察姿态误差演化、模式切换和 runtime 快照联动。';
      }
      if (view === 'preview') {
        return '<strong>当前工作台：Dashboard 预览。</strong> 这里直接预览可独立分发的静态结果界面。';
      }
      return '<strong>当前工作台：结果总览。</strong> 先看结果驾驶舱、通过率、最佳 run 和当前运行状态，再进入更细的对比与回放。';
    }

    function resultsOverviewSummaryText(view) {
      if (view === 'browser') {
        return '现在显示结果列表。这里按主线、通过率和实验名筛选结果。';
      }
      if (view === 'focus') {
        return '现在显示当前结果。这里细读摘要、图表、诊断和运行状态。';
      }
      return '现在显示结果驾驶舱。这里先做首页判断，再决定去列表还是当前结果。';
    }

    function resultsBrowserSummaryText(view) {
      if (view === 'map') {
        return '现在显示结果地图。这里按协议阶段查看哪些主线已经形成代表结果，哪些还缺稳定入口。';
      }
      if (view === 'risk') {
        return '现在显示风险结果，优先收拢通过率偏低、失败 run 较多、需要先诊断的实验。';
      }
      if (view === 'recent') {
        return '现在显示最近结果，适合按时间回看刚跑完的实验，并快速决定是否进入当前结果细读。';
      }
      return '默认先看代表结果，先锁定各条主线最适合继续阅读的入口，再进入完整结果表。';
    }

    function overviewWorkbenchSummaryText(view) {
      if (view === 'route') {
        return '现在显示实验主线。这里先固定看标准实验路径、showcase 路径和当前最适合继续推进的研究方向。';
      }
      if (view === 'assets') {
        return '现在显示当前资产。这里固定看最近计划、最近结果、待关注事项和工作区资产密度。';
      }
      return '默认先看平台定位，先确认平台在解决什么问题、工作流如何闭环、当前阶段适合拿它做什么。';
    }

    function libraryDetailViewNote(view) {
      if (view === 'assets') {
        return '现在显示代表资产。这里更适合看标准模板、代表计划、代表结果和当前主线该怎么继续推进。';
      }
      if (view === 'academic') {
        return '现在显示学术说明。这里集中看实验假设、适用前提、建议图表和成熟平台映射。';
      }
      return '默认先看协议概览，先确认这条实验主线在回答什么问题、推荐怎么推进、下一条协议接到哪里。';
    }

    let routeSyncLocked = false;

    function routeValue(value, allowed, fallback) {
      return allowed.includes(value) ? value : fallback;
    }

    function currentRouteParams() {
      const params = new URLSearchParams();
      const page = routeValue(state.currentPage || 'overview', ['overview', 'lab', 'results'], 'overview');
      params.set('page', page);
      if (page === 'overview') {
        params.set('overview', routeValue(state.overviewWorkbenchView || 'position', ['position', 'route', 'assets'], 'position'));
        return params.toString();
      }
      if (page === 'lab') {
        const labView = routeValue(state.labView || 'library', ['library', 'builder', 'manage'], 'library');
        params.set('lab', labView);
        if (labView === 'library') {
          params.set('library', routeValue(state.libraryView || 'route', ['route', 'map', 'demo', 'template'], 'route'));
          params.set('detail', routeValue(state.libraryDetailView || 'protocol', ['protocol', 'assets', 'academic'], 'protocol'));
          if (state.libraryExperimentId) params.set('experiment', state.libraryExperimentId);
          return params.toString();
        }
        if (labView === 'builder') {
          params.set('builderStage', routeValue(state.builderStage || 'question', ['question', 'variables', 'runtime', 'review'], 'question'));
          params.set('builderBench', routeValue(state.builderWorkbenchView || 'focus', ['focus', 'scale', 'deliverables'], 'focus'));
          return params.toString();
        }
        const manageView = routeValue(state.manageView || 'pool', ['pool', 'editor', 'history', 'workspace'], 'pool');
        params.set('manage', manageView);
        if (manageView === 'pool') {
          params.set('managePool', routeValue(state.managePoolView || 'focus', ['focus', 'roadmap', 'assets', 'plans'], 'focus'));
          params.set('manageCockpit', routeValue(state.manageCockpitView || 'focus', ['focus', 'health', 'attention'], 'focus'));
          params.set('manageWorkbench', routeValue(state.manageWorkbenchView || 'focus', ['focus', 'roadmap', 'assets', 'actions'], 'focus'));
        }
        if (manageView === 'editor') {
          params.set('editorBench', routeValue(state.editorWorkbenchView || 'context', ['context', 'edit', 'support'], 'context'));
          params.set('editorMode', routeValue(state.editorViewMode || 'overview', ['overview', 'json'], 'overview'));
          params.set('editorContext', routeValue(state.editorContextView || 'profile', ['profile', 'brief', 'roadmap'], 'profile'));
        }
        return params.toString();
      }
      const resultsView = routeValue(state.resultsView || 'overview', ['overview', 'compare', 'replay', 'preview'], 'overview');
      params.set('results', resultsView);
      if (resultsView === 'overview') {
        const overviewView = routeValue(state.resultsOverviewView || 'cockpit', ['cockpit', 'browser', 'focus'], 'cockpit');
        params.set('resultsOverview', overviewView);
        if (overviewView === 'cockpit') {
          params.set('resultsCockpit', routeValue(state.resultsCockpitView || 'focus', ['focus', 'risk', 'route'], 'focus'));
        } else if (overviewView === 'browser') {
          params.set('resultsBrowser', routeValue(state.resultsBrowserView || 'representative', ['representative', 'map', 'risk', 'recent'], 'representative'));
        } else {
          params.set('resultSummary', routeValue(state.resultSummaryView || 'overview', ['overview', 'diagnostics', 'figures', 'roadmap', 'artifacts'], 'overview'));
          params.set('resultWorkbench', routeValue(state.resultWorkbenchView || 'focus', ['focus', 'route', 'actions'], 'focus'));
          params.set('resultDetail', routeValue(state.resultDetailView || 'summary', ['summary', 'runs', 'detail'], 'summary'));
        }
      } else if (resultsView === 'compare') {
        params.set('compareMode', routeValue(state.compareViewMode || 'brief', ['brief', 'runs', 'charts'], 'brief'));
        params.set('compareWorkbench', routeValue(state.compareWorkbenchView || 'focus', ['focus', 'params', 'replay'], 'focus'));
      } else if (resultsView === 'replay') {
        params.set('replayMode', routeValue(state.replayViewMode || 'animation', ['animation', 'mission', 'runtime'], 'animation'));
        params.set('replayWorkbench', routeValue(state.replayWorkbenchView || 'focus', ['focus', 'mission', 'runtime'], 'focus'));
      } else if (resultsView === 'preview') {
        params.set('previewMode', routeValue(state.previewViewMode || 'brief', ['brief', 'frame', 'archive'], 'brief'));
      }
      return params.toString();
    }

    function currentRouteHash() {
      return `#${currentRouteParams()}`;
    }

    function hydrateStateFromHash() {
      const raw = window.location.hash.replace(/^#/, '').trim() || window.location.search.replace(/^\\?/, '').trim();
      if (!raw) return false;
      const params = new URLSearchParams(raw);
      const page = routeValue(params.get('page') || state.currentPage || 'overview', ['overview', 'lab', 'results'], 'overview');
      state.currentPage = page;
      state.overviewWorkbenchView = routeValue(params.get('overview') || state.overviewWorkbenchView || 'position', ['position', 'route', 'assets'], 'position');
      if (page === 'lab') {
        state.labView = routeValue(params.get('lab') || state.labView || 'library', ['library', 'builder', 'manage'], 'library');
        state.libraryView = routeValue(params.get('library') || state.libraryView || 'route', ['route', 'map', 'demo', 'template'], 'route');
        state.libraryDetailView = routeValue(params.get('detail') || state.libraryDetailView || 'protocol', ['protocol', 'assets', 'academic'], 'protocol');
        state.libraryExperimentId = params.get('experiment') || state.libraryExperimentId || '';
        state.builderStage = routeValue(params.get('builderStage') || state.builderStage || 'question', ['question', 'variables', 'runtime', 'review'], 'question');
        state.builderWorkbenchView = routeValue(params.get('builderBench') || state.builderWorkbenchView || 'focus', ['focus', 'scale', 'deliverables'], 'focus');
        state.manageView = routeValue(params.get('manage') || state.manageView || 'pool', ['pool', 'editor', 'history', 'workspace'], 'pool');
        state.managePoolView = routeValue(params.get('managePool') || state.managePoolView || 'focus', ['focus', 'roadmap', 'assets', 'plans'], 'focus');
        state.manageCockpitView = routeValue(params.get('manageCockpit') || state.manageCockpitView || 'focus', ['focus', 'health', 'attention'], 'focus');
        state.manageWorkbenchView = routeValue(params.get('manageWorkbench') || state.manageWorkbenchView || 'focus', ['focus', 'roadmap', 'assets', 'actions'], 'focus');
        state.editorWorkbenchView = routeValue(params.get('editorBench') || state.editorWorkbenchView || 'context', ['context', 'edit', 'support'], 'context');
        state.editorViewMode = routeValue(params.get('editorMode') || state.editorViewMode || 'overview', ['overview', 'json'], 'overview');
        state.editorContextView = routeValue(params.get('editorContext') || state.editorContextView || 'profile', ['profile', 'brief', 'roadmap'], 'profile');
      }
      if (page === 'results') {
        state.resultsView = routeValue(params.get('results') || state.resultsView || 'overview', ['overview', 'compare', 'replay', 'preview'], 'overview');
        state.resultsOverviewView = routeValue(params.get('resultsOverview') || state.resultsOverviewView || 'cockpit', ['cockpit', 'browser', 'focus'], 'cockpit');
        state.resultsCockpitView = routeValue(params.get('resultsCockpit') || state.resultsCockpitView || 'focus', ['focus', 'risk', 'route'], 'focus');
        state.resultsBrowserView = routeValue(params.get('resultsBrowser') || state.resultsBrowserView || 'representative', ['representative', 'map', 'risk', 'recent'], 'representative');
        state.resultSummaryView = routeValue(params.get('resultSummary') || state.resultSummaryView || 'overview', ['overview', 'diagnostics', 'figures', 'roadmap', 'artifacts'], 'overview');
        state.resultWorkbenchView = routeValue(params.get('resultWorkbench') || state.resultWorkbenchView || 'focus', ['focus', 'route', 'actions'], 'focus');
        state.resultDetailView = routeValue(params.get('resultDetail') || state.resultDetailView || 'summary', ['summary', 'runs', 'detail'], 'summary');
        state.compareViewMode = routeValue(params.get('compareMode') || state.compareViewMode || 'brief', ['brief', 'runs', 'charts'], 'brief');
        state.compareWorkbenchView = routeValue(params.get('compareWorkbench') || state.compareWorkbenchView || 'focus', ['focus', 'params', 'replay'], 'focus');
        state.replayViewMode = routeValue(params.get('replayMode') || state.replayViewMode || 'animation', ['animation', 'mission', 'runtime'], 'animation');
        state.replayWorkbenchView = routeValue(params.get('replayWorkbench') || state.replayWorkbenchView || 'focus', ['focus', 'mission', 'runtime'], 'focus');
        state.previewViewMode = routeValue(params.get('previewMode') || state.previewViewMode || 'brief', ['brief', 'frame', 'archive'], 'brief');
      }
      return true;
    }

    function syncRouteHash() {
      if (routeSyncLocked) return;
      const routeParams = currentRouteParams();
      const nextUrl = `${window.location.pathname}?${routeParams}#${routeParams}`;
      const currentUrl = `${window.location.pathname}${window.location.search}${window.location.hash}`;
      if (currentUrl === nextUrl) return;
      routeSyncLocked = true;
      history.replaceState(null, '', nextUrl);
      routeSyncLocked = false;
    }

    function renderRouteState() {
      renderPageView();
      renderLabView();
      renderManageView();
      renderResultsView();
      renderBuilderViewMode();
      renderBuilderStage();
      renderEditorWorkbenchView();
      renderEditorViewMode();
      if (state.workspace) {
        renderWorkspaceOverview();
        renderExperimentLibraryCards();
        renderManagePoolCockpit();
        renderDashboards();
      }
      if (state.currentExperiment || state.currentExperimentMapping) {
        renderEditorPlanContext(activePlanRecord());
      }
      if (state.currentDashboardData) {
        renderDashboardSummary(state.currentDashboardData);
        renderCompareView(state.currentDashboardData);
        renderReplayView(state.currentDashboardData);
        renderPreviewWorkbench(state.currentDashboardData);
      }
    }

    function libraryDetailWorkbenchNodes(options = {}) {
      const hasResult = Boolean(options.representativeDashboard);
      const hasPlan = Boolean(options.representativePlan);
      return [
        {
          tone: 'accent',
          step: '1. 先定协议',
          title: options.protocolLabel || '协议概览',
          body: options.protocolObjective || '先确认当前实验属于哪条研究协议、这一步到底在回答什么问题。',
          action: 'protocol',
          label: '看协议概览',
        },
        {
          tone: hasResult || hasPlan ? 'ok' : 'warm',
          step: '2. 再看资产',
          title: hasResult ? '代表结果已就绪' : hasPlan ? '代表计划已就绪' : '代表资产待落地',
          body: hasResult
            ? '当前可以直接打开代表结果继续读图、对比和回放。'
            : hasPlan
              ? '当前已有代表计划，下一步适合运行并沉淀成代表结果。'
              : '当前还没有稳定代表资产，适合先载入模板或生成第一版计划。',
          action: 'assets',
          label: '看代表资产',
        },
        {
          tone: 'accent',
          step: '3. 最后核学术口径',
          title: '学术说明',
          body: options.academicTakeaway || '最后确认实验假设、适用前提、建议图表和成熟平台映射。',
          action: 'academic',
          label: '看学术说明',
        },
      ];
    }

    function renderSidebarNav() {
      sidebarNav?.querySelectorAll('[data-page]').forEach(button => {
        const page = button.dataset.page || 'overview';
        let active = page === state.currentPage;
        if (page === 'lab') {
          active = active && (button.dataset.labView || 'library') === (state.labView || 'library');
        } else if (page === 'results') {
          active = active && (button.dataset.resultsView || 'overview') === (state.resultsView || 'overview');
        }
        button.classList.toggle('active', active);
      });
    }

    function renderLabView() {
      const current = state.labView || 'library';
      labViews.forEach(node => {
        node.hidden = node.dataset.labView !== current;
      });
      labNav?.querySelectorAll('[data-lab-view]').forEach(button => {
        button.classList.toggle('active', button.dataset.labView === current);
      });
      if (labSummaryCopy) {
        labSummaryCopy.innerHTML = labSummaryText(current);
      }
      if (labNavNote) {
        labNavNote.textContent = labNavNoteText(current);
      }
      renderLibraryView();
      if (current === 'manage') {
        renderManageView();
      }
      renderSidebarNav();
    }

    function renderManageView() {
      const current = state.manageView || 'pool';
      manageViews.forEach(node => {
        node.hidden = node.dataset.manageView !== current;
      });
      manageNav?.querySelectorAll('[data-manage-view]').forEach(button => {
        button.classList.toggle('active', button.dataset.manageView === current);
      });
      if (manageSummaryCopy) {
        manageSummaryCopy.innerHTML = manageSummaryText(current);
      }
      renderManagePoolView();
      renderManagePoolCockpit();
    }

    function renderManagePoolView() {
      const hasPlans = Boolean((state.workspace?.experiments || []).length);
      const preferred = 'focus';
      const current = ['focus', 'roadmap', 'assets', 'plans'].includes(state.managePoolView) ? state.managePoolView : preferred;
      state.managePoolView = current;
      managePoolViews.forEach(node => {
        if (node.dataset.managePoolView === 'workspace') {
          node.hidden = current === 'plans';
        } else {
          node.hidden = node.dataset.managePoolView !== current;
        }
      });
      managePoolToggle?.querySelectorAll('[data-manage-pool-view]').forEach(button => {
        button.classList.toggle('active', button.dataset.managePoolView === current);
      });
      if (managePoolSummary) {
        managePoolSummary.textContent = managePoolSummaryText(current);
      }
      if (managePoolFocusPanel) {
        managePoolFocusPanel.hidden = current !== 'focus';
      }
      if (managePoolWorkbenchPanel) {
        managePoolWorkbenchPanel.hidden = current === 'focus';
      }
      if (current === 'roadmap' || current === 'assets') {
        state.manageWorkbenchView = current;
      } else if (current === 'focus' && !hasPlans) {
        state.manageCockpitView = 'focus';
      }
    }

    function renderLibraryView() {
      const current = state.libraryView || 'route';
      libraryWorkspaceViews.forEach(node => {
        node.hidden = node.dataset.libraryView !== current;
      });
      libraryWorkspaceToggle?.querySelectorAll('[data-library-view]').forEach(button => {
        button.classList.toggle('active', button.dataset.libraryView === current);
      });
      if (libraryWorkspaceSummary) {
        libraryWorkspaceSummary.textContent = libraryWorkspaceSummaryText(current);
      }
    }

    function renderResultsView() {
      const current = state.resultsView || 'overview';
      resultsViews.forEach(node => {
        node.hidden = node.dataset.resultsView !== current;
      });
      resultsNav?.querySelectorAll('[data-results-view]').forEach(button => {
        button.classList.toggle('active', button.dataset.resultsView === current);
      });
      if (resultsSummaryCopy) {
        resultsSummaryCopy.innerHTML = resultsSummaryText(current);
      }
      renderResultsOverviewView();
      renderResultsOverviewCockpit();
      renderSidebarNav();
    }

    function renderResultsOverviewView() {
      const hasDashboards = Boolean((state.workspace?.dashboards || []).length);
      const preferred = state.currentDashboard || state.latestRunDashboard
        ? 'focus'
        : hasDashboards
          ? 'browser'
          : 'cockpit';
      const current = ['cockpit', 'browser', 'focus'].includes(state.resultsOverviewView) ? state.resultsOverviewView : preferred;
      state.resultsOverviewView = current;
      resultsOverviewViews.forEach(node => {
        node.hidden = node.dataset.resultsOverviewView !== current;
      });
      resultsOverviewToggle?.querySelectorAll('[data-results-overview-view]').forEach(button => {
        button.classList.toggle('active', button.dataset.resultsOverviewView === current);
      });
      if (resultsOverviewSummary) {
        resultsOverviewSummary.textContent = resultsOverviewSummaryText(current);
      }
    }

    function renderPageView() {
      const current = state.currentPage || 'overview';
      pageViews.forEach(node => {
        node.hidden = node.dataset.pageView !== current;
      });
      pageNav?.querySelectorAll('[data-page]').forEach(button => {
        button.classList.toggle('active', button.dataset.page === current);
      });
      if (pageSummary && pageSummaryCopy) {
        pageSummaryCopy.innerHTML = pageSummaryText(current);
      }
      renderSidebarNav();
    }

    function switchPage(page, scrollTop = true) {
      state.currentPage = page || 'overview';
      renderPageView();
      syncRouteHash();
      if (scrollTop) {
        window.scrollTo({top: 0, behavior: 'smooth'});
      }
    }

    function switchLabView(view, scrollTop = true) {
      state.labView = view || 'library';
      switchPage('lab', false);
      renderLabView();
      syncRouteHash();
      if (scrollTop) {
        window.scrollTo({top: 0, behavior: 'smooth'});
      }
    }

    function switchManageView(view, scrollTop = true) {
      state.manageView = view || 'pool';
      switchLabView('manage', false);
      renderManageView();
      syncRouteHash();
      if (scrollTop) {
        window.scrollTo({top: 0, behavior: 'smooth'});
      }
    }

    function setEditorContextView(view) {
      state.editorContextView = view || 'profile';
      renderEditorPlanContext(activePlanRecord());
      syncRouteHash();
    }

    function switchResultsView(view, scrollTop = true) {
      state.resultsView = view || 'overview';
      switchPage('results', false);
      renderResultsView();
      syncRouteHash();
      if (scrollTop) {
        window.scrollTo({top: 0, behavior: 'smooth'});
      }
    }

    function navigateTo(page, options = {}) {
      if (page === 'lab') {
        switchLabView(options.labView || state.labView || 'library', options.scrollTop !== false);
        return;
      }
      if (page === 'results') {
        switchResultsView(options.resultsView || state.resultsView || 'overview', options.scrollTop !== false);
        return;
      }
      switchPage(page || 'overview', options.scrollTop !== false);
    }

    function bindPrimaryNav(root, navigate) {
      root?.addEventListener('pointerup', event => {
        const button = event.target.closest('[data-page]');
        if (!button || !root.contains(button)) return;
        if (button.tagName === 'A') return;
        event.preventDefault();
        navigate(button);
      });
    }

    function switchManagePoolView(view) {
      state.managePoolView = view || 'focus';
      renderManagePoolView();
      renderManageWorkbenchSummary();
      syncRouteHash();
    }

    function switchManageCockpitView(view) {
      state.manageCockpitView = view || 'focus';
      renderManagePoolCockpit();
      syncRouteHash();
    }

    function switchManageWorkbenchView(view) {
      state.manageWorkbenchView = view || 'focus';
      renderManageWorkbenchSummary();
      syncRouteHash();
    }

    function switchResultsCockpitView(view) {
      state.resultsCockpitView = view || 'focus';
      renderResultsOverviewCockpit();
      syncRouteHash();
    }

    function switchLibraryView(view) {
      state.libraryView = view || 'route';
      renderLibraryView();
      syncRouteHash();
    }

    function switchResultsOverviewView(view) {
      state.resultsOverviewView = view || 'cockpit';
      renderResultsOverviewView();
      syncRouteHash();
    }

    function setLibraryDetailView(view) {
      state.libraryDetailView = view || 'protocol';
      renderExperimentLibraryCards();
      syncRouteHash();
    }

    function pushActivity(title, detail, tone = 'info') {
      state.activity.unshift({
        title,
        detail,
        tone,
        time: new Date().toLocaleTimeString('zh-CN', {hour12: false}),
      });
      state.activity = state.activity.slice(0, 8);
      renderActivityFeed();
    }

    function renderActivityFeed() {
      if (!state.activity.length) {
        activityFeed.innerHTML = '<div class="empty">平台的查看、校验、创建和运行操作会记录在这里。</div>';
        if (activityFeedWorkspace) {
          activityFeedWorkspace.innerHTML = '<div class="empty">平台的查看、校验、创建和运行操作会记录在这里。</div>';
        }
        return;
      }
      const markup = state.activity.map(item => `
        <div class="activity-item">
          <span>${esc(item.time)} · ${esc(item.tone)}</span>
          <strong>${esc(item.title)}</strong>
          <p>${esc(item.detail)}</p>
        </div>
      `).join('');
      activityFeed.innerHTML = markup;
      if (activityFeedWorkspace) {
        activityFeedWorkspace.innerHTML = markup;
      }
    }

    function experimentScaleText(plan) {
      if (!plan) return '—';
      return plan.error ? '计划异常' : `${plan.sweeps ? '参数扫描' : '单场景'} · MC ${plan.monte_carlo_samples || 0}`;
    }

    function experimentRunCount(plan) {
      if (!plan) return null;
      const direct = Number(plan.runs);
      if (Number.isFinite(direct) && direct > 0) return direct;
      const sweeps = Number(plan.sweeps);
      const mc = Number(plan.monte_carlo_samples);
      return Math.max(1, Number.isFinite(sweeps) && sweeps > 0 ? sweeps : 1) * Math.max(1, Number.isFinite(mc) && mc > 0 ? mc : 1);
    }

    function experimentOptionLabel(plan) {
      if (!plan) return '';
      const resultTag = matchingDashboardForPlan(plan) ? ' · 有结果' : '';
      return `${plan.name} · ${plan.scenario || '—'} · run ${experimentRunCount(plan)}${resultTag}`;
    }

    function experimentOptionMarkup(rows, selectedPath) {
      return rows.map(plan => {
        const active = plan.path === selectedPath ? ' selected' : '';
        return `<option value="${esc(plan.path)}"${active}>${esc(experimentOptionLabel(plan))}</option>`;
      }).join('');
    }

    function renderBuilderResult() {
      const info = state.builderLastCreated;
      const action = state.builderAction;
      const error = state.builderError;
      if (action) {
        builderResult.hidden = false;
        builderResult.className = 'callout info busy';
        builderResultTitle.textContent = action.mode === 'run'
          ? '正在创建并运行实验'
          : '正在创建实验计划';
        builderResultBody.textContent = action.mode === 'run'
          ? `平台正在创建 ${action.name} 并准备执行仿真。创建完成后会自动切换到结果区，并把最新结果高亮出来。`
          : `平台正在创建 ${action.name} 并进行基础校验。完成后会自动载入下面的实验计划编辑器。`;
        builderResultOpenPlan.disabled = true;
        builderResultRun.disabled = true;
        builderResultOpenResult.disabled = true;
        return;
      }
      if (error) {
        builderResult.hidden = false;
        builderResult.className = 'callout danger';
        builderResultTitle.textContent = '创建实验时遇到问题';
        builderResultBody.textContent = info?.path
          ? `${error} 计划 ${info.name} 仍然可用，你可以先查看计划或再次运行。`
          : `${error} 你可以检查场景、扫描变量和输出目录后再试一次。`;
        builderResultOpenPlan.disabled = !info?.path;
        builderResultRun.disabled = !info?.path;
        builderResultOpenResult.disabled = !info?.dashboard;
        return;
      }
      if (!info) {
        builderResult.hidden = true;
        builderResult.className = 'callout success';
        builderResultTitle.textContent = '实验计划已创建';
        builderResultBody.textContent = '这里会显示刚创建好的实验计划、运行规模和后续操作入口。';
        builderResultOpenPlan.disabled = true;
        builderResultRun.disabled = true;
        builderResultOpenResult.disabled = true;
        return;
      }
      builderResult.hidden = false;
      builderResult.className = info.resolved_from_collision ? 'callout warning' : 'callout success';
      builderResultTitle.textContent = info.dashboard
        ? (info.source_path ? '实验计划副本已创建并运行' : '实验计划已创建并运行')
        : (info.source_path ? '实验计划副本已创建' : '实验计划已创建');
      const summaryParts = [
        `计划 ${info.name} 已保存到 ${info.path}。`,
        `当前校验规模为 ${info.validation?.runs ?? '—'} 个 run。`,
        info.resolved_from_collision
          ? `原名称 ${info.requested_name} 已存在，平台自动改名为 ${info.name}。`
          : `输出目录为 ${info.output_root}。`,
      ];
      if (info.dashboard) {
        summaryParts.push(`结果界面已经生成，可以直接查看 ${info.dashboard}。`);
      } else {
        summaryParts.push('你现在可以直接查看计划，或者继续点击运行。');
      }
      if (info.source_path) {
        summaryParts.push(`这个副本来自 ${info.source_path}，适合拿来做参数变体或任务对比。`);
      }
      builderResultBody.textContent = summaryParts.join(' ');
      builderResultOpenPlan.disabled = !info.path;
      builderResultRun.disabled = !info.path;
      builderResultOpenResult.disabled = !info.dashboard;
    }

    function syncCreateButtons() {
      const busy = Boolean(state.builderAction);
      createPlanButton.disabled = busy;
      createPlanRunButton.disabled = busy;
      createPlanButton.textContent = busy ? '正在创建...' : '创建实验计划';
      createPlanRunButton.textContent = busy
        ? (state.builderAction?.mode === 'run' ? '正在运行...' : '正在创建并运行...')
        : '创建并运行';
    }

    function renderBuilderViewMode() {
      builderViewToggle?.querySelectorAll('button').forEach(button => {
        button.classList.toggle('active', button.dataset.builderView === state.builderViewMode);
      });
      document.querySelectorAll('[data-builder-view-mode]').forEach(node => {
        const shouldShow = state.builderViewMode === 'advanced' || node.dataset.builderViewMode !== 'advanced';
        node.hidden = !shouldShow;
      });
      if (builderViewSummary) {
        builderViewSummary.textContent = state.builderViewMode === 'advanced'
          ? '当前显示完整实验配置，适合微调输出目录、Monte Carlo、参考目标和底层变量路径。'
          : '默认先显示常用配置，保持界面简洁；需要更细粒度控制时再展开高级项。';
      }
    }

    function renderBuilderStage() {
      const stage = state.builderStage || 'question';
      const summary = {
        question: {
          title: '当前步骤：研究问题',
          body: '先确定实验要回答什么问题，再选择基线场景和推荐模板。这样后面的变量设计和结果报告会更清楚。',
        },
        variables: {
          title: '当前步骤：场景与变量',
          body: '在这一阶段明确扫描变量、推荐取值和 Monte Carlo 规模，把研究问题展开成可运行的 run 集合。',
        },
        runtime: {
          title: '当前步骤：任务与验收',
          body: '在这里定义 mission、mode、参考目标和通过标准，让实验计划具备工程上可复查的任务语义。',
        },
        review: {
          title: '当前步骤：预览与生成',
          body: '最后集中确认规模、输出目录和实验说明，然后把它固化为实验计划，或直接创建并运行。',
        },
      }[stage] || {
        title: '当前步骤：研究问题',
        body: '先确定实验要回答什么问题，再选择基线场景和推荐模板。',
      };
      builderStageNav?.querySelectorAll('[data-builder-stage-target]').forEach(button => {
        button.classList.toggle('active', button.dataset.builderStageTarget === stage);
      });
      builderStagePanels.forEach(panel => {
        panel.hidden = panel.dataset.builderStage !== stage;
      });
      if (builderStageSummary) {
        builderStageSummary.innerHTML = `<strong>${esc(summary.title)}</strong><p>${esc(summary.body)}</p>`;
      }
      renderBuilderWorkbenchSummary();
    }

    function switchBuilderStage(stage) {
      state.builderStage = stage || 'question';
      renderBuilderStage();
      syncRouteHash();
    }

    function switchBuilderWorkbenchView(view) {
      state.builderWorkbenchView = view || 'focus';
      renderBuilderWorkbenchSummary();
      syncRouteHash();
    }

    function renderEditorWorkbenchView() {
      const current = ['context', 'edit', 'support'].includes(state.editorWorkbenchView) ? state.editorWorkbenchView : 'context';
      state.editorWorkbenchView = current;
      editorWorkbenchToggle?.querySelectorAll('[data-editor-workbench-view]').forEach(button => {
        button.classList.toggle('active', button.dataset.editorWorkbenchView === current);
      });
      editorWorkbenchPanels.forEach(panel => {
        panel.hidden = panel.dataset.editorWorkbenchView !== current;
      });
      if (editorWorkbenchSummary) {
        editorWorkbenchSummary.textContent = current === 'context'
          ? '默认先看计划画像，先确认这份计划在实验主线中的位置、研究问题和下一步动作，再进入字段级编辑。'
          : current === 'edit'
            ? '这里专门做计划结构编辑。先看结构概览，需要精确改字段时再切到 JSON。'
            : '这里固定收纳当前场景和操作记录，便于做编辑前复查与运行后追踪。';
      }
    }

    function switchEditorWorkbenchView(view) {
      state.editorWorkbenchView = view || 'context';
      renderEditorWorkbenchView();
      syncRouteHash();
    }

    function renderEditorViewMode() {
      const hasPlan = Boolean(state.currentExperiment);
      editorViewToggle?.querySelectorAll('button').forEach(button => {
        button.disabled = !hasPlan;
        button.classList.toggle('active', button.dataset.editorView === state.editorViewMode);
      });
      const jsonMode = state.editorViewMode === 'json';
      editorOverview.hidden = jsonMode;
      editorText.hidden = !jsonMode;
      editorHelp.textContent = jsonMode
        ? '编辑器会按 JSON 保存，并在保存前执行严格校验。相对场景路径会按计划文件所在目录解析。'
        : '结构概览默认帮助你快速确认实验计划意图；需要精确修改字段时再切换到 JSON。';
      if (editorViewSummary) {
        editorViewSummary.textContent = !hasPlan
          ? '先从左侧实验计划列表载入一个计划。'
          : jsonMode
            ? '当前显示原始 JSON，适合精确修改 runtime、mission、acceptance 等字段。'
            : '默认先显示实验计划概览，只有在需要精确修改字段时再切到 JSON。';
      }
    }

    function editorPlanMeta(mapping) {
      const sweeps = Array.isArray(mapping?.sweeps) ? mapping.sweeps.length : 0;
      const mc = Number(mapping?.monte_carlo?.samples || 0);
      const runs = Math.max(1, sweeps || 1) * Math.max(1, mc || 1);
      return {
        name: mapping?.metadata?.name || editorTitle.textContent,
        scenario: mapping?.scenario || '—',
        runs,
        sweeps,
        monteCarlo: mc,
      };
    }

    function refreshEditorOverviewFromMapping() {
      const mapping = state.currentExperimentMapping;
      if (!mapping) {
        editorOverview.innerHTML = '载入实验计划后，这里会显示结构化概览。';
        return;
      }
      const meta = editorPlanMeta(mapping);
      editorOverview.innerHTML = editorOverviewHtml(mapping, {
        name: meta.name,
        scenario: meta.scenario,
        runs: meta.runs,
        sweeps: meta.sweeps,
        monte_carlo_samples: meta.monteCarlo,
      });
    }

    function syncEditorTextFromMapping(markDirty = true) {
      if (!state.currentExperimentMapping) return;
      editorText.value = `${JSON.stringify(state.currentExperimentMapping, null, 2)}\n`;
      refreshEditorOverviewFromMapping();
      renderEditorPlanContext(activePlanRecord());
      if (markDirty) {
        state.currentExperimentDirty = true;
        updateEditorButtons();
      }
    }

    function applyEditorStructuredFields() {
      const mapping = state.currentExperimentMapping;
      if (!mapping || !state.currentExperiment) return;
      const sweepPath = document.getElementById('editor-quick-sweep-path')?.value || '';
      const sweepValuesText = document.getElementById('editor-quick-sweep-values')?.value || '';
      const outputRoot = document.getElementById('editor-quick-output-root')?.value || '';
      const mcSamplesText = document.getElementById('editor-quick-mc-samples')?.value || '';
      const mcSeedText = document.getElementById('editor-quick-mc-seed')?.value || '';
      const missionTemplate = document.getElementById('editor-quick-mission-template')?.value || 'single_mode';
      const mode = document.getElementById('editor-quick-mode')?.value || 'inertial_hold';
      const reference = document.getElementById('editor-quick-reference')?.value || 'body_zero';
      const detumbleS = Number(document.getElementById('editor-quick-detumble')?.value || 0.5);
      const runtimeTemplate = document.getElementById('editor-quick-runtime-template')?.value || 'single_rate';
      const maxFinal = document.getElementById('editor-quick-accept-final')?.value;
      const maxRms = document.getElementById('editor-quick-accept-rms')?.value;
      const maxTorque = document.getElementById('editor-quick-accept-torque')?.value;

      mapping.runtime = mapping.runtime || {};
      mapping.runtime.template = runtimeTemplate;
      mapping.outputs = mapping.outputs || {};
      if (String(outputRoot).trim()) {
        mapping.outputs.root = outputRoot;
      } else {
        delete mapping.outputs.root;
      }

      const sweepValues = _parseEditorList(sweepValuesText);
      if (String(sweepPath).trim() && sweepValues.length) {
        mapping.sweeps = [{path: sweepPath, values: sweepValues}];
      } else {
        mapping.sweeps = [];
      }

      const mcSamples = Number(mcSamplesText || 0);
      if (mcSamples > 0) {
        mapping.monte_carlo = {samples: mcSamples};
        if (String(mcSeedText).trim() !== '') {
          mapping.monte_carlo.seed = Number(mcSeedText);
        }
      } else {
        delete mapping.monte_carlo;
      }

      if (missionTemplate === 'detumble_then_hold') {
        mapping.mission = {
          template: 'detumble_then_hold',
          detumble_s: detumbleS,
          hold_mode: mode,
          reference,
        };
      } else {
        mapping.mission = {
          template: 'single_mode',
          mode,
          reference,
        };
      }

      const acceptance = {};
      if (maxFinal !== undefined && String(maxFinal).trim() !== '') acceptance.max_final_error_deg = Number(maxFinal);
      if (maxRms !== undefined && String(maxRms).trim() !== '') acceptance.max_rms_error_deg = Number(maxRms);
      if (maxTorque !== undefined && String(maxTorque).trim() !== '') acceptance.max_peak_torque_nm = Number(maxTorque);
      if (Object.keys(acceptance).length) {
        mapping.acceptance = acceptance;
      } else {
        delete mapping.acceptance;
      }

      syncEditorTextFromMapping(true);
    }

    function _parseEditorList(text) {
      const raw = String(text || '').trim();
      if (!raw) return [];
      return raw.split(',').map(item => item.trim()).filter(Boolean).map(item => {
        const numeric = Number(item);
        return Number.isFinite(numeric) && item !== '' ? numeric : item;
      });
    }

    function editorSweepHint(path) {
      const text = String(path || '').trim();
      if (!text) return '当前未启用参数扫描，计划会按单一配置运行。';
      if (text.startsWith('controller.')) return `当前扫描 ${text}，适合做控制器整定与稳定性比较。`;
      if (text.startsWith('time.')) return `当前扫描 ${text}，适合做随机种子和可重复性分析。`;
      if (text.includes('reaction_wheels')) return `当前扫描 ${text}，适合比较执行机构能力和饱和影响。`;
      return `当前扫描 ${text}，平台会按每个候选值展开独立 run。`;
    }

    function editorMissionHint(template, mode) {
      if (template === 'detumble_then_hold') {
        return `当前任务会先消旋，再切到 ${mode || '目标保持模式'}，适合展示模式切换和收敛过程。`;
      }
      return `当前任务会全程保持 ${mode || '目标模式'}，适合稳态性能验证和参数对比。`;
    }

    function editorStructuredSummaryHtml(mapping) {
      const sweeps = Array.isArray(mapping?.sweeps) ? mapping.sweeps : [];
      const firstSweep = sweeps[0] || {};
      const sweepValues = Array.isArray(firstSweep.values) ? firstSweep.values : [];
      const sweepCount = sweepValues.length || 1;
      const mcSamples = Math.max(Number(mapping?.monte_carlo?.samples || 0), 0);
      const mcCount = mcSamples > 0 ? mcSamples : 1;
      const runCount = sweepCount * mcCount;
      const mission = mapping?.mission || {};
      const missionMode = mission.template === 'detumble_then_hold'
        ? (mission.hold_mode || 'inertial_hold')
        : (mission.mode || 'inertial_hold');
      return `
        <div class="summary-grid" style="margin-top:10px">
          <div class="summary-card"><span>预计 run 数</span><strong>${runCount}</strong></div>
          <div class="summary-card"><span>扫描取值数量</span><strong>${sweepValues.length || 1}</strong></div>
          <div class="summary-card"><span>Monte Carlo</span><strong>${mcSamples > 0 ? mcSamples : '未启用'}</strong></div>
          <div class="summary-card"><span>任务流程</span><strong>${mission.template === 'detumble_then_hold' ? '消旋后保持' : '单模式保持'}</strong></div>
        </div>
        <div class="detail-grid">
          <div class="detail-box"><strong>扫描提示</strong><div>${esc(editorSweepHint(firstSweep.path || ''))}</div></div>
          <div class="detail-box"><strong>任务提示</strong><div>${esc(editorMissionHint(mission.template, missionMode))}</div></div>
        </div>
      `;
    }

    function resetEditorStructuredGroup(group) {
      const mapping = state.currentExperimentMapping;
      if (!mapping) return;
      if (group === 'sweep') {
        mapping.sweeps = [];
        delete mapping.monte_carlo;
        if (mapping.outputs && Object.keys(mapping.outputs).length === 0) delete mapping.outputs;
      } else if (group === 'mission') {
        mapping.mission = {
          template: 'single_mode',
          mode: 'inertial_hold',
          reference: 'body_zero',
        };
        mapping.runtime = {template: 'single_rate'};
      } else if (group === 'acceptance') {
        delete mapping.acceptance;
      }
      syncEditorTextFromMapping(true);
    }

    function editorOverviewHtml(mapping, plan) {
      if (!mapping || typeof mapping !== 'object') {
        return '载入实验计划后，这里会显示结构化概览。';
      }
      const metadata = mapping.metadata || {};
      const outputs = mapping.outputs || {};
      const sweeps = Array.isArray(mapping.sweeps) ? mapping.sweeps : [];
      const monteCarlo = mapping.monte_carlo || {};
      const mission = mapping.mission || {};
      const runtime = mapping.runtime || {};
      const acceptance = mapping.acceptance || {};
      const scenario = plan?.scenario || mapping.scenario || '—';
      const missionLabel = mission.template === 'detumble_then_hold'
        ? '消旋后保持'
        : mission.template === 'single_mode'
          ? '单模式保持'
          : (mission.template || '未指定');
      const runtimeLabel = runtime.template || runtime.name || 'single_rate / 默认';
      const missionMode = mission.template === 'detumble_then_hold'
        ? (mission.hold_mode || 'inertial_hold')
        : (mission.mode || 'inertial_hold');
      const missionReference = mission.reference || 'body_zero';
      const detumbleS = mission.detumble_s ?? 0.5;
      const sweepPath = sweeps[0]?.path || '';
      const sweepValuesText = Array.isArray(sweeps[0]?.values) ? sweeps[0].values.join(',') : '';
      const acceptanceItems = Object.entries(acceptance).filter(([, value]) => value !== null && value !== undefined && value !== '');
      const sweepItems = sweeps.length
        ? sweeps.map(item => {
            const values = Array.isArray(item.values) ? item.values.join('、') : '—';
            return `<div class="detail-box"><strong>${esc(item.path || '未命名扫描')}</strong><div>${esc(values)}</div></div>`;
          }).join('')
        : '<div class="detail-box"><strong>参数扫描</strong><div>当前计划没有启用参数扫描。</div></div>';
      const acceptanceHtml = acceptanceItems.length
        ? acceptanceItems.map(([key, value]) => `<span class="chip">${esc(key)}: ${esc(value)}</span>`).join('')
        : '<span class="subtle">当前没有显式验收阈值。</span>';
      return `
        <div class="summary-grid">
          <div class="summary-card"><span>计划名称</span><strong>${esc(plan?.name || metadata.name || '—')}</strong></div>
          <div class="summary-card"><span>场景</span><strong>${esc(scenario)}</strong></div>
          <div class="summary-card"><span>运行规模</span><strong>${esc(plan ? `run ${experimentRunCount(plan)}` : '未校验')}</strong></div>
          <div class="summary-card"><span>输出目录</span><strong title="${esc(outputs.root || '—')}">${esc(outputs.root || '—')}</strong></div>
        </div>
        <div class="detail-grid">
          <div class="detail-box"><strong>任务模板</strong><div>${esc(missionLabel)}</div></div>
          <div class="detail-box"><strong>运行时模板</strong><div>${esc(runtimeLabel)}</div></div>
          <div class="detail-box"><strong>Monte Carlo</strong><div>${monteCarlo.samples ? `样本 ${esc(monteCarlo.samples)} / seed ${esc(monteCarlo.seed ?? '—')}` : '未启用'}</div></div>
          <div class="detail-box"><strong>说明</strong><div>${esc(metadata.description || '当前计划没有填写描述。')}</div></div>
        </div>
        <div class="detail-box" style="margin-bottom:10px"><strong>参数扫描</strong><div class="subtle">这里展示当前计划的 sweep 配置，便于快速确认实验批次规模。</div></div>
        <div class="detail-grid">${sweepItems}</div>
        <div class="detail-box" style="margin-top:10px"><strong>验收规则</strong><div class="chips">${acceptanceHtml}</div></div>
        <div class="detail-box" style="margin-top:10px">
          <strong>常用配置</strong>
          <div class="subtle">这里可以直接调整任务模板、运行时模板、参数扫描、Monte Carlo、输出目录和常用验收阈值，修改会自动同步到 JSON。</div>
          ${editorStructuredSummaryHtml(mapping)}
          <div class="quick-edit-grid" id="editor-quick-form">
            <div class="quick-edit-section">
              <strong>输出与批量实验</strong>
              <p>管理 sweep、Monte Carlo 和输出目录，决定实验会生成多少 run。</p>
              <div class="quick-edit-grid">
                <label>输出目录
                  <input id="editor-quick-output-root" value="${esc(outputs.root || '')}" oninput="applyEditorStructuredFields()">
                </label>
                <label>扫描变量路径
                  <input id="editor-quick-sweep-path" value="${esc(sweepPath)}" oninput="applyEditorStructuredFields()">
                </label>
                <label>扫描取值
                  <input id="editor-quick-sweep-values" value="${esc(sweepValuesText)}" oninput="applyEditorStructuredFields()">
                </label>
                <label>Monte Carlo 样本数
                  <input id="editor-quick-mc-samples" type="number" min="0" value="${esc(monteCarlo.samples ?? 0)}" oninput="applyEditorStructuredFields()">
                </label>
                <label>Monte Carlo 种子
                  <input id="editor-quick-mc-seed" type="number" value="${esc(monteCarlo.seed ?? '')}" oninput="applyEditorStructuredFields()">
                </label>
              </div>
              <div class="toolbar" style="margin-top:10px">
                <button class="secondary" type="button" onclick="resetEditorStructuredGroup('sweep')">清空批量配置</button>
              </div>
            </div>
            <div class="quick-edit-section">
              <strong>任务与运行时</strong>
              <p>管理 mission / runtime 常用项，决定实验流程和模式切换。</p>
              <div class="quick-edit-grid">
                <label>任务模板
                  <select id="editor-quick-mission-template" onchange="applyEditorStructuredFields()">
                    <option value="single_mode" ${mission.template !== 'detumble_then_hold' ? 'selected' : ''}>单模式保持</option>
                    <option value="detumble_then_hold" ${mission.template === 'detumble_then_hold' ? 'selected' : ''}>消旋后保持</option>
                  </select>
                </label>
                <label>运行时模板
                  <select id="editor-quick-runtime-template" onchange="applyEditorStructuredFields()">
                    <option value="single_rate" ${String(runtime.template || 'single_rate') === 'single_rate' ? 'selected' : ''}>single_rate</option>
                  </select>
                </label>
                <label>${mission.template === 'detumble_then_hold' ? '保持模式' : '任务模式'}
                  <select id="editor-quick-mode" onchange="applyEditorStructuredFields()">
                    <option value="inertial_hold" ${missionMode === 'inertial_hold' ? 'selected' : ''}>惯性保持 inertial_hold</option>
                    <option value="sun_pointing" ${missionMode === 'sun_pointing' ? 'selected' : ''}>太阳指向 sun_pointing</option>
                    <option value="earth_pointing" ${missionMode === 'earth_pointing' ? 'selected' : ''}>对地指向 earth_pointing</option>
                    <option value="safe" ${missionMode === 'safe' ? 'selected' : ''}>安全模式 safe</option>
                  </select>
                </label>
                <label>参考目标
                  <select id="editor-quick-reference" onchange="applyEditorStructuredFields()">
                    <option value="body_zero" ${missionReference === 'body_zero' ? 'selected' : ''}>机体零姿态 body_zero</option>
                    <option value="sun" ${missionReference === 'sun' ? 'selected' : ''}>太阳参考 sun</option>
                    <option value="nadir" ${missionReference === 'nadir' ? 'selected' : ''}>对地参考 nadir</option>
                  </select>
                </label>
                <label ${mission.template === 'detumble_then_hold' ? '' : 'hidden'}>消旋时长 s
                  <input id="editor-quick-detumble" type="number" step="0.1" value="${esc(detumbleS)}" oninput="applyEditorStructuredFields()">
                </label>
              </div>
              <div class="toolbar" style="margin-top:10px">
                <button class="secondary" type="button" onclick="resetEditorStructuredGroup('mission')">恢复默认流程</button>
              </div>
            </div>
            <div class="quick-edit-section">
              <strong>验收规则</strong>
              <p>用一组轻量阈值快速定义当前实验的通过标准。</p>
              <div class="quick-edit-grid">
                <label>末端误差阈值 deg
                  <input id="editor-quick-accept-final" type="number" step="0.1" value="${esc(acceptance.max_final_error_deg ?? '')}" oninput="applyEditorStructuredFields()">
                </label>
                <label>RMS 误差阈值 deg
                  <input id="editor-quick-accept-rms" type="number" step="0.1" value="${esc(acceptance.max_rms_error_deg ?? '')}" oninput="applyEditorStructuredFields()">
                </label>
                <label>峰值力矩阈值 N m
                  <input id="editor-quick-accept-torque" type="number" step="0.01" value="${esc(acceptance.max_peak_torque_nm ?? '')}" oninput="applyEditorStructuredFields()">
                </label>
              </div>
              <div class="toolbar" style="margin-top:10px">
                <button class="secondary" type="button" onclick="resetEditorStructuredGroup('acceptance')">清空验收规则</button>
              </div>
            </div>
          </div>
        </div>
      `;
    }

    function ensureRunProgressTimer() {
      if (runProgressTimer || !state.runAction) return;
      runProgressTimer = window.setInterval(() => {
        if (!state.runAction) {
          window.clearInterval(runProgressTimer);
          runProgressTimer = null;
          return;
        }
        renderRunStatusPanels();
      }, 400);
    }

    function stopRunProgressTimer() {
      if (runProgressTimer) {
        window.clearInterval(runProgressTimer);
        runProgressTimer = null;
      }
    }

    function stageSnapshot(action) {
      const now = Date.now();
      const startedAt = Number(action?.startedAt || now);
      const elapsedS = Math.max(0, (now - startedAt) / 1000);
      const stages = action?.mode === 'rerun'
        ? ['准备重跑', '写入输出目录', '执行仿真', '刷新结果']
        : ['准备计划', '写入输出目录', '执行仿真', '刷新结果'];
      const stageDurationS = 1.7;
      const rawIndex = Math.floor(elapsedS / stageDurationS);
      const activeIndex = Math.min(stages.length - 1, rawIndex);
      const stageElapsed = elapsedS - activeIndex * stageDurationS;
      const stageProgress = Math.min(0.95, Math.max(0, stageElapsed / stageDurationS));
      const completedStages = Math.min(activeIndex, stages.length - 1);
      const progress = Math.min(
        0.92,
        ((completedStages + stageProgress) / stages.length)
      );
      return {
        stages,
        activeIndex,
        elapsedS,
        progress,
      };
    }

    function latestMetricOverviewHtml(latest) {
      const rows = Array.isArray(latest?.runs) ? [...latest.runs] : [];
      if (!rows.length) {
        return '<div class="detail-box" style="margin-top:12px"><strong>关键指标概览</strong><div class="subtle">当前摘要没有附带 run 级数据。打开结果详情后，这里会显示更直观的误差分布。</div></div>';
      }
      const ranked = rows
        .filter(row => Number.isFinite(Number(row.final_error_deg)))
        .sort((a, b) => Number(a.final_error_deg) - Number(b.final_error_deg))
        .slice(0, 5);
      const maxValue = Math.max(...ranked.map(row => Number(row.final_error_deg || 0)), 0.0001);
      const rowsHtml = ranked.map(row => {
        const value = Number(row.final_error_deg || 0);
        const width = Math.max(6, (value / maxValue) * 100);
        return `
          <div class="metric-row">
            <span class="metric-label">${esc(row.run_id || '—')}</span>
            <div class="metric-bar"><span style="width:${width}%"></span></div>
            <span class="metric-value">${fmt(value)} deg</span>
          </div>
        `;
      }).join('');
      return `
        <div class="detail-box" style="margin-top:12px">
          <strong>关键指标概览</strong>
          <div class="subtle" style="margin-top:4px">按末端误差从优到劣展示前 5 个 run，方便快速判断当前实验的收敛分布。</div>
          <div class="metric-overview">${rowsHtml}</div>
        </div>
      `;
    }

    function recentDashboardTrendHtml(selectedPath = null) {
      const rows = [...(state.workspace?.dashboards || [])]
        .sort((a, b) => Number(a.updated_ts || 0) - Number(b.updated_ts || 0))
        .slice(-6);
      if (!rows.length) return '';
      const width = 360;
      const height = 104;
      const innerHeight = 74;
      const barWidth = rows.length ? Math.max(18, Math.floor((width - 24) / rows.length) - 8) : 24;
      const xStep = rows.length ? (width - 24) / rows.length : 40;
      const bars = rows.map((row, index) => {
        const rate = Math.max(0, Math.min(1, Number(row.acceptance_rate || 0)));
        const barHeight = Math.max(6, Math.round(rate * innerHeight));
        const x = 14 + index * xStep;
        const y = 12 + (innerHeight - barHeight);
        const fill = row.path === selectedPath
          ? '#124e78'
          : rate >= 0.999
            ? '#2f7d50'
            : rate >= 0.8
              ? '#b96a10'
              : '#ab2d2d';
        const label = esc(row.name || `run-${index + 1}`);
        const rateText = `${Math.round(rate * 1000) / 10}%`;
        return `
          <g>
            <title>${label} · 通过率 ${rateText}</title>
            <rect x="${x}" y="${y}" width="${barWidth}" height="${barHeight}" rx="5" fill="${fill}" opacity="${row.path === selectedPath ? '1' : '0.88'}"></rect>
          </g>
        `;
      }).join('');
      const captions = [
        rows[0],
        rows[Math.max(0, Math.floor((rows.length - 1) / 2))],
        rows[rows.length - 1],
      ].filter(Boolean).map(row => `<span>${esc((row.updated_at || '').slice(5, 16) || row.name || '')}</span>`).join('');
      return `
        <div class="trend-panel">
          <strong>最近实验通过率趋势</strong>
          <p>按最近 6 次结果展示通过率变化，深色柱表示当前选中的结果或最新结果。</p>
          <svg class="trend-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="最近实验通过率趋势图">
            <line x1="10" y1="${12 + innerHeight}" x2="${width - 10}" y2="${12 + innerHeight}" stroke="#d4dce8" stroke-width="2"></line>
            ${bars}
          </svg>
          <div class="trend-caption">${captions}</div>
        </div>
      `;
    }

    function parameterColumnLabel(name) {
      const raw = String(name || '').replace(/^param_/, '');
      const mapping = {
        'controller.pd_kp': '比例增益 pd_kp',
        'controller.pd_kd': '微分增益 pd_kd',
        'system.controller': '控制器类型',
        'system.environment': '环境模型',
        'system.disturbance_profile': '扰动配置模板',
        'sensors.gyro.noise_std_rad_s': '陀螺噪声强度',
        'time.seed': '随机种子',
        'monte_carlo.sample': 'Monte Carlo 样本序号',
        'actuators.reaction_wheels.max_torque_nm': '轮组最大力矩',
        'actuators.reaction_wheels.momentum_gain': '轮组动量管理增益',
      };
      return mapping[raw] || raw;
    }

    function resultThemeLabel(result) {
      const params = Array.isArray(result?.parameter_columns) ? result.parameter_columns : [];
      if (params.includes('param_system.controller')) return '控制器 benchmark';
      if (params.includes('param_system.environment')) return '环境敏感性';
      if (params.includes('param_system.disturbance_profile')) return '扰动分解';
      if (params.includes('param_sensors.gyro.noise_std_rad_s')) return '测量敏感性';
      if (params.includes('param_controller.pd_kp') || params.includes('param_controller.pd_kd')) return '控制器整定';
      if (params.includes('param_time.seed') || params.includes('param_monte_carlo.sample')) return '鲁棒性';
      if (params.includes('param_actuators.reaction_wheels.momentum_gain')) return '轮速管理';
      if (params.includes('param_actuators.reaction_wheels.max_torque_nm')) return '执行器边界';
      if (result?.timeline?.steps?.length) return '任务模式切换';
      return '通用实验';
    }

    function resultJudgementText(result) {
      const rate = Number(result?.acceptance_rate || 0);
      if (rate >= 0.999) return '当前实验整体通过，适合作为稳定基线或继续做更细粒度对比。';
      if (rate >= 0.8) return '当前实验大部分 run 可接受，但仍存在边界工况，适合继续缩小参数范围或复查最差 run。';
      return '当前实验存在明显失败工况，说明这组变量或任务配置还不适合作为稳定基线。';
    }

    function resultBestObservationText(result) {
      const best = result?.best_run || {};
      const worst = result?.worst_run || {};
      if (best['param_system.controller']) {
        const bestController = String(best['param_system.controller']);
        const worstController = worst['param_system.controller'] ? String(worst['param_system.controller']) : '另一控制器';
        return `当前最佳控制器为 ${bestController}，最佳末端误差 ${fmt(best.final_error_deg)} deg；可重点比较它与 ${worstController} 的误差和控制动作差异。`;
      }
      if (best['param_system.environment']) {
        return `当前最佳环境配置为 ${esc(best['param_system.environment'])}，建议重点对比 zero 与 orbital 环境下的误差、控制力矩和扰动力矩预算。`;
      }
      if (best['param_system.disturbance_profile']) {
        return `当前最佳扰动配置为 ${esc(best['param_system.disturbance_profile'])}，建议重点比较不同扰动模板下主导扰动项和误差退化顺序。`;
      }
      if (best['param_controller.pd_kp'] !== undefined) {
        return `当前最佳参数取值对应的比例增益为 ${esc(best['param_controller.pd_kp'])}，最佳末端误差 ${fmt(best.final_error_deg)} deg。`;
      }
      if (best['param_controller.pd_kd'] !== undefined) {
        return `当前最佳参数取值对应的微分增益为 ${esc(best['param_controller.pd_kd'])}，最佳 RMS 误差 ${fmt(best.rms_error_deg)} deg。`;
      }
      if (best['param_sensors.gyro.noise_std_rad_s'] !== undefined) {
        return `当前最佳测量质量对应的陀螺噪声为 ${esc(best['param_sensors.gyro.noise_std_rad_s'])} rad/s，建议继续关注最差噪声档位下的误差退化。`;
      }
      if (best['param_actuators.reaction_wheels.momentum_gain'] !== undefined) {
        return `当前最优动量管理增益为 ${esc(best['param_actuators.reaction_wheels.momentum_gain'])}，适合继续联合轮速余量与姿态误差一起判断。`;
      }
      if (best['param_actuators.reaction_wheels.max_torque_nm'] !== undefined) {
        return `当前最优执行器能力取值为 ${esc(best['param_actuators.reaction_wheels.max_torque_nm'])} Nm，建议继续结合峰值力矩与饱和风险一起判断。`;
      }
      return `当前最佳 run 为 ${esc(result?.best_run_id || '—')}，最佳末端误差 ${fmt(result?.best_final_error_deg)} deg。`;
    }

    function resultNextStepText(result) {
      const theme = resultThemeLabel(result);
      if (theme === '控制器 benchmark') return '下一步建议把最佳控制器固定下来，再进入参数整定或故障/鲁棒性实验，形成统一 benchmark 链路。';
      if (theme === '环境敏感性') return '下一步建议把 orbital 环境下表现最差的配置继续带到轮组能力、任务模式切换或 Monte Carlo 实验中，确认环境扰动是否会放大工程边界问题。';
      if (theme === '扰动分解') return '下一步建议把主导扰动项最大的配置继续带到更复杂任务或轮组能力实验中，确认哪类环境扰动最值得优先建模和约束。';
      if (theme === '测量敏感性') return '下一步建议把敏感性最差的噪声档位带到更复杂场景里，确认传感器质量边界是否仍可接受。';
      if (theme === '控制器整定') return '下一步建议围绕当前最优参数附近缩小扫描范围，或切到 Monte Carlo 检查整定后的鲁棒性。';
      if (theme === '鲁棒性') return '下一步建议复查最差 run，并把相同配置放到更真实的故障场景或轨道环境中继续验证。';
      if (theme === '轮速管理') return '下一步建议联动查看轮速、动量利用和姿态误差，判断动量回收策略是否开始干扰主姿态任务。';
      if (theme === '执行器边界') return '下一步建议把能力边界与轮速、饱和和任务模式切换结果联合起来判断工程余量。';
      if (theme === '任务模式切换') return '下一步建议结合 mode timeline、runtime schedule 和姿态回放确认过渡段是否满足任务预期。';
      return '下一步建议把当前实验沉淀为标准模板，再继续扩充变量、任务和鲁棒性检查。';
    }

    function resultAcademicBriefHtml(result, matchedPlan = null) {
      const failureCounts = aggregateFailureReasons(result.runs || []);
      const failureText = failureCounts.length
        ? failureCounts.map(([reason, count]) => `${acceptanceReasonLabel(reason)} × ${count}`).join('；')
        : '当前没有失败 run，可以把结论优先沉淀为代表结果。';
      const figurePackages = resultFigurePackages(result);
      const primaryPackages = figurePackages.slice(0, 2).map(pkg => `${pkg.label} ${pkg.title}`).join(' / ') || '当前没有推荐图包';
      const directCurrentId = findCuratedExperimentIdForResult(result);
      const roadmap = experimentRoadmapProfile(directCurrentId, resultThemeLabel(result));
      const activeId = directCurrentId || roadmap.ids[0] || '';
      const activeIndex = roadmap.ids.indexOf(activeId);
      const nextId = activeIndex >= 0 ? (roadmap.ids[activeIndex + 1] || '') : '';
      const nextConfig = curatedExperimentConfig(nextId);
      const nextLabel = nextConfig?.label || '当前先回到实验链路整理代表结论';
      return `
        <div class="history-detail" style="margin-bottom:10px">
          <strong>学术摘要首屏</strong>
          <p>先压结论、验收、图包和下一实验，再进入图表、链路和回放。</p>
          <div class="detail-grid" style="margin-top:10px">
            <div class="detail-box">
              <strong>一句结论</strong>
              <div>${esc(resultBestObservationText(result))} ${esc(resultJudgementText(result))}</div>
            </div>
            <div class="detail-box">
              <strong>验收判定</strong>
              <div>通过率 ${Math.round((result.acceptance_rate || 0) * 1000) / 10}% · ${esc(failureText)}</div>
            </div>
            <div class="detail-box">
              <strong>优先图包</strong>
              <div>${esc(primaryPackages)}</div>
            </div>
            <div class="detail-box">
              <strong>下一条实验</strong>
              <div>${esc(nextLabel)} · ${esc(resultNextStepText(result))}</div>
            </div>
          </div>
          <div class="toolbar" style="margin-top:10px">
            <button type="button" onclick="setResultSummaryView('figures')">去学术图包</button>
            <button class="secondary" type="button" onclick="setResultSummaryView('roadmap')">看实验链路</button>
            <button class="secondary" type="button" onclick="${matchedPlan ? `showExperiment('${esc(matchedPlan.path)}')` : 'void(0)'}" ${matchedPlan ? '' : 'disabled'}>查看对应计划</button>
          </div>
        </div>
      `;
    }

    function resultResearchHtml(result) {
      const experimentId = findCuratedExperimentIdForResult(result);
      const config = curatedExperimentConfig(experimentId);
      const parameterLabels = (result.parameter_columns || []).map(parameterColumnLabel);
      const academicMeta = experimentAcademicMeta(experimentId, config || {theme: resultThemeLabel(result)});
      const standardMeta = experimentStandardCardMeta(
        experimentId,
        config || {planName: result.experiment_name || result.name, theme: resultThemeLabel(result)},
        academicMeta,
      );
      const brief = experimentBriefMeta({
        experimentId,
        config: config || {planName: result.experiment_name || result.name, theme: resultThemeLabel(result)},
        academicMeta,
        standardMeta,
        question: result.description || config?.question || '当前实验重点是比较不同配置对闭环稳定性和控制性能的影响。',
        scenarioPath: resolveCuratedExperimentScenario(config),
        variableSummary: parameterLabels.length
          ? `当前结果主要围绕 ${parameterLabels.join(' / ')} 展开。${parameterLabels.length > 1 ? '适合继续用关键 Run 对比确认变量之间的差异。' : '适合继续看变量变化对最佳/最差 run 的影响。'}`
          : `当前结果属于 ${resultThemeLabel(result)}，适合继续看代表图包和关键 Run 入口。`,
        acceptanceSummary: Number(result.failed_count || 0) > 0
          ? `当前结果通过率 ${percentText(result.acceptance_rate)}，失败 ${fmt(result.failed_count)} 个 run。建议先按失败原因和最差工况复查。`
          : `当前结果通过率 ${percentText(result.acceptance_rate)}，当前没有失败 run，可直接沉淀代表结论。`,
        figuresSummary: standardMeta.figurePackage || academicMeta.plots,
        nextSummary: resultNextStepText(result),
      });
      return experimentBriefHtml(brief, {
        title: '实验结论导读',
        lead: '把当前结果重新落回与实验库、创建器一致的 7 项说明里，再继续读图、对比和回放。',
        compact: true,
      });
    }

    function resultTakeawayCardsHtml(result, matchedPlan = null) {
      const experimentId = findCuratedExperimentIdForResult(result);
      const config = curatedExperimentConfig(experimentId);
      const academicMeta = experimentAcademicMeta(experimentId, config || {theme: resultThemeLabel(result)});
      const protocol = experimentProtocolConfig(protocolIdForExperiment(experimentId));
      const figurePackages = resultFigurePackages(result);
      const primaryFigures = figurePackages.slice(0, 2).map(pkg => `${pkg.label} ${pkg.title}`).join(' / ') || '当前没有稳定图包入口。';
      const roadmap = experimentRoadmapProfile(experimentId, resultThemeLabel(result));
      const activeId = experimentId || roadmap.ids[0] || '';
      const activeIndex = roadmap.ids.indexOf(activeId);
      const nextId = activeIndex >= 0 ? (roadmap.ids[activeIndex + 1] || '') : '';
      const nextConfig = curatedExperimentConfig(nextId);
      const failureCounts = aggregateFailureReasons(result.runs || []);
      const failureText = failureCounts.length
        ? failureCounts.map(([reason, count]) => `${acceptanceReasonLabel(reason)} × ${count}`).join('；')
        : '当前没有失败 run，可作为代表结果继续沉淀。';
      return `
        <div class="history-detail" style="margin:10px 0 0">
          <strong>代表结论卡</strong>
          <p>首屏只保留最适合展示和汇报的判断，避免第一次进入结果页就掉进长说明。</p>
          <div class="detail-grid" style="margin-top:10px">
            <div class="detail-box"><strong>一句话结论</strong><div>${esc(academicMeta.takeaway || resultBestObservationText(result))}</div></div>
            <div class="detail-box"><strong>验收状态</strong><div>通过率 ${percentText(result.acceptance_rate)} · ${esc(failureText)}</div></div>
            <div class="detail-box"><strong>协议阶段</strong><div>${esc(protocol?.label || '未映射标准协议')} · ${esc(protocol?.decision || '当前结果应支撑下一步实验判断。')}</div></div>
            <div class="detail-box"><strong>展示图包</strong><div>${esc(primaryFigures)}</div></div>
            <div class="detail-box"><strong>对应计划</strong><div>${esc(matchedPlan ? `${matchedPlan.name} · ${experimentScaleText(matchedPlan)}` : '当前还没有稳定计划绑定。')}</div></div>
            <div class="detail-box"><strong>下一条实验</strong><div>${esc(nextConfig ? `${nextConfig.label}：${nextConfig.question || nextConfig.description}` : resultNextStepText(result))}</div></div>
          </div>
        </div>
      `;
    }

    function resultSemanticCardsHtml(result, matchedPlan = null, options = {}) {
      if (!result) return '';
      const planProfile = matchedPlan ? planAssetProfile(matchedPlan) : {curatedId: '', line: resultThemeLabel(result)};
      const experimentId = findCuratedExperimentIdForResult(result) || planProfile.curatedId || '';
      const config = curatedExperimentConfig(experimentId);
      const theme = config?.theme || planProfile.line || resultThemeLabel(result);
      const academicMeta = experimentAcademicMeta(experimentId, config || {theme});
      const standardMeta = experimentStandardCardMeta(
        experimentId,
        config || {planName: result.experiment_name || result.name, theme},
        academicMeta,
      );
      const brief = experimentBriefMeta({
        experimentId,
        config: config || {planName: result.experiment_name || result.name, theme},
        academicMeta,
        standardMeta,
        question: result.description || config?.question || '当前实验重点是比较不同配置对闭环稳定性和控制性能的影响。',
        scenarioPath: resolveCuratedExperimentScenario(config) || result.scenario_name || result.scenario || '',
        variableSummary: (result.parameter_columns || []).length
          ? `当前结果主要围绕 ${(result.parameter_columns || []).map(parameterColumnLabel).join(' / ')} 展开。建议用关键 Run 对比确认变量差异。`
          : `当前结果属于 ${resultThemeLabel(result)}，适合先用代表图包和关键 Run 入口做阅读。`,
        acceptanceSummary: Number(result.failed_count || 0) > 0
          ? `当前结果通过率 ${percentText(result.acceptance_rate)}，失败 ${fmt(result.failed_count)} 个 run。建议先看失败原因、最差工况和诊断页。`
          : `当前结果通过率 ${percentText(result.acceptance_rate)}，当前没有失败 run，可优先沉淀代表结论。`,
        figuresSummary: standardMeta.figurePackage || academicMeta.plots,
        nextSummary: resultNextStepText(result),
      });
      const readingTask = experimentReadingTaskMeta({
        experimentId,
        config: config || {planName: result.experiment_name || result.name, theme},
        academicMeta,
        standardMeta,
        theme,
        nextAction: Number(result.failed_count || 0) > 0
          ? '先看诊断与最差 run，再去结果对比或回到计划管理收紧变量、任务和验收。'
          : '先看概要和图包，再进入结果对比、姿态回放，最后把结果沉淀为代表资产。',
        deliverables: 'README.md、index.json、summary_metrics.csv、dashboard.html',
      });
      const binding = standardAssetBindingRecord({
        result,
        plan: matchedPlan,
        dashboards: options.dashboards,
      });
      return `
        ${experimentBriefHtml(brief, {
          title: options.briefTitle || '当前结果实验说明卡',
          lead: options.briefLead || '结果层也直接复用实验库的 7 项说明，避免一到结果页就只剩指标和文件名。',
          compact: true,
        })}
        ${experimentReadingTaskHtml(readingTask, {
          title: options.readingTitle || '当前结果阅读任务书',
          lead: options.readingLead || '把当前结果先看什么、再看什么、最后回到哪里直接写在结果入口层。',
          compact: true,
        })}
        ${standardAssetBindingHtml(binding, {
          title: options.bindingTitle || '当前结果主线资产链',
          showToolbar: options.showToolbar === true,
        })}
      `;
    }

    function experimentAcademicShowcaseHtml(activeConfig, academicMeta, activeProtocol) {
      return `
        <div class="history-detail" style="margin-top:12px; margin-bottom:0">
          <strong>实验展示卡</strong>
          <p>先用一组短卡回答“这个实验适合怎么展示、重点讲什么、下一步接哪里”，再进入完整学术说明。</p>
          <div class="detail-grid" style="margin-top:10px">
            <div class="detail-box"><strong>展示重点</strong><div>${esc(academicMeta.takeaway || activeConfig?.description || '当前实验用于形成一版可复查实验结论。')}</div></div>
            <div class="detail-box"><strong>重点图表</strong><div>${esc(academicMeta.plots || '姿态误差 / 力矩 / 通过率')}</div></div>
            <div class="detail-box"><strong>适用对象</strong><div>${esc(activeConfig?.whenToUse || '适合做平台演示、结果汇报或作为后续派生实验起点。')}</div></div>
            <div class="detail-box"><strong>下一协议判断</strong><div>${esc(activeProtocol?.decision || '当前实验应给出是否继续推进下一阶段主线的判断。')}</div></div>
          </div>
        </div>
      `;
    }

    function resultRoadmapHtml(result) {
      const directCurrentId = findCuratedExperimentIdForResult(result);
      const theme = resultThemeLabel(result);
      const roadmap = experimentRoadmapProfile(directCurrentId, theme);
      const activeId = directCurrentId || roadmap.ids[0] || '';
      const assetBinding = standardAssetBindingRecord({experimentId: activeId, result});
      const activeIndex = roadmap.ids.indexOf(activeId);
      const activeConfig = curatedExperimentConfig(activeId);
      const activeMeta = experimentLibraryDetailMeta(activeId, activeConfig);
      const nextId = activeIndex >= 0 ? (roadmap.ids[activeIndex + 1] || '') : '';
      const nextConfig = curatedExperimentConfig(nextId);
      const introText = directCurrentId
        ? `当前结果已匹配到标准实验 ${activeConfig?.label || activeId}，可以直接沿这条实验主线继续推进。`
        : `当前结果暂未直接匹配标准实验计划，平台已按“${theme}”推断最接近的实验主线，方便继续沉淀为标准资产。`;
      const steps = roadmap.ids.map((id, index) => {
        const config = curatedExperimentConfig(id);
        const detail = experimentLibraryDetailMeta(id, config);
        const isCurrent = index === activeIndex;
        const isUpcoming = index > activeIndex;
        const statusLabel = directCurrentId
          ? (index < activeIndex ? '前置实验' : isCurrent ? '当前所处环节' : '推荐下一步')
          : (isCurrent ? '推荐起点' : '后续阶段');
        const cls = isCurrent ? 'current' : isUpcoming ? 'upcoming' : '';
        return `
          <div class="roadmap-step ${cls}">
            <span>${esc(statusLabel)} · 第 ${index + 1} 步</span>
            <strong>${esc(config?.label || id)}</strong>
            <p>${esc(config?.question || config?.description || '当前实验用于形成可复现实验资产。')}</p>
            <p>${esc(detail.platform)}</p>
          </div>
        `;
      }).join('');
      return `
        <div class="detail-box" style="margin-top:10px">
          <strong>实验链路导航</strong>
          <div class="subtle" style="margin-top:4px">把当前结果放回标准实验主线里，帮助我们判断它位于哪一层、下一步该往哪里扩。</div>
          <div class="callout" style="margin-top:10px; margin-bottom:0">
            <strong>${esc(roadmap.label)}</strong>
            <p>${esc(roadmap.description)} ${esc(introText)}</p>
          </div>
          <div class="roadmap-stack">${steps}</div>
          <div class="detail-grid" style="margin-top:10px">
            <div class="detail-box">
              <strong>当前所处环节</strong>
              <div>${esc(activeMeta.baseline)}</div>
            </div>
            <div class="detail-box">
              <strong>继续到这组实验</strong>
              <div>${esc(nextConfig ? (experimentLibraryDetailMeta(nextId, nextConfig).baseline || nextConfig.description || '继续沿实验链路推进。') : activeMeta.next)}</div>
            </div>
          </div>
          ${standardAssetBindingHtml(assetBinding, {title: '当前结果的标准资产绑定'})}
          <div class="toolbar" style="margin-top:10px">
            <button type="button" onclick="${activeId ? `previewRoadmapExperiment('${esc(activeId)}')` : 'void(0)'}" ${activeId ? '' : 'disabled'}>在实验库中定位</button>
            <button class="secondary" type="button" onclick="${activeId ? `openRoadmapExperiment('${esc(activeId)}')` : 'void(0)'}" ${activeId ? '' : 'disabled'}>打开当前实验</button>
            <button class="secondary" type="button" onclick="${nextId ? `previewRoadmapExperiment('${esc(nextId)}')` : 'void(0)'}" ${nextId ? '' : 'disabled'}>继续到这组实验</button>
          </div>
        </div>
      `;
    }

    function resultGuideHtml(result, {latest = false} = {}) {
      const matchedPlan = [...(state.workspace?.experiments || [])].find(plan => matchingDashboardForPlan(plan)?.path === result.path);
      const hasReplay = Boolean(result.compare_histories && Object.keys(result.compare_histories).length);
      const hasCompare = Array.isArray(result.compare_run_ids) && result.compare_run_ids.length > 1;
      const suggestions = [
        `先看最佳 run ${result.best_run_id || '—'} 和通过率，快速判断这次实验是否达到预期。`,
        hasCompare ? '再看 Run 排行与关键 run 对比，确认参数差异和最差工况。'
          : '当前 run 数较少，可以直接从摘要跳到结果预览或计划配置。',
        hasReplay ? '最后看姿态回放和任务时间线，适合做演示讲解或复查模式切换。'
          : '如果需要更多动态细节，可以重新运行并保留完整时序数据。',
      ];
      return `
        <div class="detail-box" style="margin-top:10px">
          <strong>结果导览</strong>
          <div class="subtle" style="margin-top:4px">平台已经帮你把这次实验结果组织好了，推荐按下面顺序浏览。</div>
          <div class="chips" style="margin-top:8px">
            <span class="chip">${latest ? '最新运行结果' : '当前结果'}</span>
            ${matchedPlan ? '<span class="chip">已匹配实验计划</span>' : '<span class="chip">未匹配实验计划</span>'}
            ${hasCompare ? '<span class="chip">支持关键 Run 对比</span>' : '<span class="chip">当前对比维度较少</span>'}
            ${hasReplay ? '<span class="chip">支持姿态回放</span>' : '<span class="chip">回放数据有限</span>'}
          </div>
          <div class="detail-grid" style="margin-top:10px">
            ${suggestions.map((text, index) => `<div class="detail-box"><strong>第 ${index + 1} 步</strong><div>${esc(text)}</div></div>`).join('')}
          </div>
          <div class="toolbar" style="margin-top:10px">
            <button type="button" onclick="showDashboard('${esc(result.path)}')">查看结果详情</button>
            <button class="secondary" type="button" onclick="scrollToRunWorkbench()">定位到 Run 排行</button>
            <button class="secondary" type="button" onclick="scrollToReplayView()">定位到姿态回放</button>
            <button class="secondary" type="button" onclick="${matchedPlan ? `showExperiment('${esc(matchedPlan.path)}')` : 'void(0)'}" ${matchedPlan ? '' : 'disabled'}>查看对应计划</button>
          </div>
        </div>
      `;
    }

    function resultFigureCards(result) {
      const currentId = findCuratedExperimentIdForResult(result);
      const currentConfig = curatedExperimentConfig(currentId);
      const theme = resultThemeLabel(result);
      const compareReady = Array.isArray(result.compare_run_ids) && result.compare_run_ids.length > 1;
      const replayReady = Boolean(result.compare_histories && Object.keys(result.compare_histories).length);
      const baseCards = [
        {
          label: '图 1',
          title: 'Run 排行与边界工况',
          purpose: '先从 run 排行锁定最佳、最差和边界工况，确认这次实验的有效参数区间或失败工况位置。',
          reading: '如果最差 run 与最佳 run 差距很大，优先回看边界变量与验收失败原因。',
          actionLabel: '查看 Run 排行',
          actionCode: 'scrollToRunWorkbench()',
        },
      ];
      const themeCards = {
        '控制器整定': [
          {
            label: '图 2',
            title: '姿态误差对比曲线',
            purpose: '比较不同参数组合的收敛速度、振荡程度和稳态误差。',
            reading: '末端误差下降但 RMS 仍高，通常说明过渡段振荡尚未压住。',
            actionLabel: '打开结果对比',
            actionCode: compareReady ? 'scrollToCompareView()' : '',
          },
          {
            label: '图 3',
            title: '控制力矩对比曲线',
            purpose: '确认更激进的参数是否以更大的控制动作换取了误差改善。',
            reading: '若误差改善有限但峰值力矩明显上升，说明参数已开始逼近执行器边界。',
            actionLabel: '查看姿态回放',
            actionCode: replayReady ? 'scrollToReplayView()' : '',
          },
        ],
        '鲁棒性': [
          {
            label: '图 2',
            title: '最差工况代表性曲线',
            purpose: '围绕最差 run 查看误差与控制动作的时序退化形态。',
            reading: '如果最差工况与最佳工况形态完全不同，说明随机边界触发了另一类动态过程。',
            actionLabel: '打开结果对比',
            actionCode: compareReady ? 'scrollToCompareView()' : '',
          },
          {
            label: '图 3',
            title: '静态 Dashboard 汇总',
            purpose: '在 dashboard 中快速查看通过率、最佳/最差 run 和整体分布摘要。',
            reading: '适合做汇报或把统计结论整理成可归档页面。',
            actionLabel: '预览 Dashboard',
            actionCode: 'scrollToPreviewView()',
          },
        ],
        '控制器 benchmark': [
          {
            label: '图 2',
            title: '控制器 A/B 对比图',
            purpose: '直接比较不同控制律在相同任务下的误差收敛和控制动作。',
            reading: '关注是否存在一方误差更小但控制更激进，或另一方更稳但响应偏慢。',
            actionLabel: '打开结果对比',
            actionCode: compareReady ? 'scrollToCompareView()' : '',
          },
          {
            label: '图 3',
            title: '结果汇总页',
            purpose: '把控制器结论压缩成结果摘要，便于后续固定平台默认基线。',
            reading: '优先记录最佳控制器、关键指标和下一步整定入口。',
            actionLabel: '回到概要',
            actionCode: "setResultSummaryView('overview')",
          },
        ],
        '环境敏感性': [
          {
            label: '图 2',
            title: '环境对比曲线',
            purpose: '比较 zero 与 orbital 条件下误差、控制力矩和扰动力矩预算的差异。',
            reading: '如果 orbital 明显恶化误差或力矩，就值得继续做扰动分解。',
            actionLabel: '打开结果对比',
            actionCode: compareReady ? 'scrollToCompareView()' : '',
          },
          {
            label: '图 3',
            title: '扰动诊断摘要',
            purpose: '快速定位哪类环境因素最可能主导当前误差退化。',
            reading: '把失败原因、主导扰动项和最差 run 解释一起看，判断是否继续进入更细的环境建模。',
            actionLabel: '查看诊断',
            actionCode: "setResultSummaryView('diagnostics')",
          },
        ],
        '扰动分解': [
          {
            label: '图 2',
            title: '主导扰动项对照',
            purpose: '比较不同扰动模板下哪一项最先主导扰动力矩预算。',
            reading: '主导项稳定出现时，说明后续高保真建模应优先落在这类扰动上。',
            actionLabel: '查看诊断',
            actionCode: "setResultSummaryView('diagnostics')",
          },
          {
            label: '图 3',
            title: '环境到执行器链路',
            purpose: '把扰动分解结论带回实验链路，决定是否继续做执行器余量实验。',
            reading: '适合把环境判断推进到工程能力边界判断。',
            actionLabel: '查看实验链路',
            actionCode: "setResultSummaryView('roadmap')",
          },
        ],
        '测量敏感性': [
          {
            label: '图 2',
            title: '噪声档位误差曲线',
            purpose: '比较测量噪声上升后误差和通过率是如何退化的。',
            reading: '如果误差退化先于通过率下降，说明还有一定工程容错空间。',
            actionLabel: '打开结果对比',
            actionCode: compareReady ? 'scrollToCompareView()' : '',
          },
          {
            label: '图 3',
            title: '代表性姿态过程',
            purpose: '回看最差噪声档位在动态过程中的误差抖动和控制响应。',
            reading: '适合判断是观测噪声主导，还是控制律本身对噪声放大过敏。',
            actionLabel: '查看姿态回放',
            actionCode: replayReady ? 'scrollToReplayView()' : '',
          },
        ],
        '任务模式切换': [
          {
            label: '图 2',
            title: '姿态回放与模式时间线',
            purpose: '观察 detumble、保持和目标模式切换过程是否与 mode timeline 一致。',
            reading: '如果视觉回放和时间线一致且过渡平滑，说明 mission 设计更可解释。',
            actionLabel: '查看姿态回放',
            actionCode: replayReady ? 'scrollToReplayView()' : '',
          },
          {
            label: '图 3',
            title: '调度与报告页面',
            purpose: '在 dashboard 中检查 runtime、mission 和结果报告是否形成一致的叙事。',
            reading: '适合向老师或团队展示“这不仅是脚本，而是平台工作流”。',
            actionLabel: '预览 Dashboard',
            actionCode: 'scrollToPreviewView()',
          },
        ],
        '执行器边界': [
          {
            label: '图 2',
            title: '能力边界对比曲线',
            purpose: '比较执行器能力变化对末端误差、峰值力矩和最差工况的影响。',
            reading: '如果力矩或饱和先出问题，说明瓶颈更偏执行机构而不是控制参数。',
            actionLabel: '打开结果对比',
            actionCode: compareReady ? 'scrollToCompareView()' : '',
          },
          {
            label: '图 3',
            title: '姿态回放与边界状态',
            purpose: '观察边界工况下的动态过程是否已经出现明显不稳定或控制迟滞。',
            reading: '适合辅助判断是否需要更保守的任务或更强执行器。',
            actionLabel: '查看姿态回放',
            actionCode: replayReady ? 'scrollToReplayView()' : '',
          },
        ],
        '轮速管理': [
          {
            label: '图 2',
            title: '轮速与误差联动',
            purpose: '联合查看动量管理相关工况的姿态误差和长期执行器余量。',
            reading: '如果轮速回拉明显改善余量且误差未恶化，说明策略是正向有效的。',
            actionLabel: '查看姿态回放',
            actionCode: replayReady ? 'scrollToReplayView()' : '',
          },
          {
            label: '图 3',
            title: '执行器边界对照',
            purpose: '把轮速管理结论带回执行器边界主线，判断是否还需更高能力轮组。',
            reading: '适合把短时闭环结论推进到长期工程可持续运行判断。',
            actionLabel: '查看实验链路',
            actionCode: "setResultSummaryView('roadmap')",
          },
        ],
      };
      const cards = [...baseCards, ...((themeCards[theme] || [
        {
          label: '图 2',
          title: '关键 run 对比',
          purpose: '比较代表性工况的误差与控制动作差异。',
          reading: '优先确认差异来自参数、环境还是任务流程。',
          actionLabel: '打开结果对比',
          actionCode: compareReady ? 'scrollToCompareView()' : '',
        },
        {
          label: '图 3',
          title: '静态结果归档页',
          purpose: '使用 dashboard 汇总当前实验的主要结论与文件产物。',
          reading: '适合做整理、归档和对外展示。',
          actionLabel: '预览 Dashboard',
          actionCode: 'scrollToPreviewView()',
        },
      ]))];
      if (currentConfig?.theme === '执行器边界' && cards.length >= 3) {
        cards[1].purpose = '比较不同执行器能力档位下的误差、力矩和主导扰动变化。';
      }
      return cards;
    }

    function resultFigurePackages(result) {
      const theme = resultThemeLabel(result);
      const cards = resultFigureCards(result);
      const compareReady = Array.isArray(result.compare_run_ids) && result.compare_run_ids.length > 1;
      const replayReady = Boolean(result.compare_histories && Object.keys(result.compare_histories).length);
      const themeCopy = {
        '控制器整定': {
          baseline: '先确认最佳参数点、边界参数点和验收状态，再进入误差与力矩权衡。',
          diagnostics: '重点查最差 run、失败原因和峰值量，判断问题是振荡、过激控制还是参数区间太窄。',
          boundary: '把误差改善与峰值力矩放在一起看，回答“性能提升是否值得这份控制代价”。',
          replay: '回到动态过程确认收敛速度、振荡和控制动作是否与前面的图表结论一致。',
        },
        '鲁棒性': {
          baseline: '先确认通过率、最佳-最差差距和统计边界，再决定是否值得继续放大随机因素。',
          diagnostics: '重点查最差工况、失败原因和主导扰动，判断边界工况是否具有一致退化模式。',
          boundary: '把统计差距带回关键对比，回答“问题来自随机因素、环境还是控制余量”。',
          replay: '回看最差工况的动态过程，确认边界 run 是否真的触发了另一类姿态行为。',
        },
        '控制器 benchmark': {
          baseline: '先锁定最佳控制器和代表 run，再进入误差与控制动作的正面对比。',
          diagnostics: '重点看失败原因和动态峰值，避免只看最终误差而忽略控制代价。',
          boundary: '把控制器优劣带回指标权衡，回答“更优控制器是否也更稳、更省动作”。',
          replay: '用回放核对代表控制器的动态过程，便于做演示和默认基线选择。',
        },
        '环境敏感性': {
          baseline: '先确认环境切换是否已经改变结论，再决定是否继续做扰动分解。',
          diagnostics: '重点查主导扰动项、失败原因和最差 run，判断环境是否已成为主要约束。',
          boundary: '把环境退化带回关键对比，回答“是环境重要，还是控制/执行器余量更重要”。',
          replay: '回看环境更重的一侧在动态过程中的误差和力矩变化，辅助工程判断。',
        },
        '扰动分解': {
          baseline: '先确认哪类扰动模板最值得继续跟踪，再展开主导扰动与预算分析。',
          diagnostics: '重点看主导扰动项、扰动力矩峰值和失败工况，形成高保真建模优先级。',
          boundary: '把扰动结论送回实验链路，回答是否该继续扩到执行器能力边界实验。',
          replay: '回到动态过程核对主导扰动出现时的姿态退化形态，而不只停留在静态预算表。',
        },
        '测量敏感性': {
          baseline: '先确认噪声档位与通过率/误差退化顺序，再决定感知链是否是主瓶颈。',
          diagnostics: '重点查最差噪声档位和失败原因，判断问题更偏估计还是控制放大。',
          boundary: '把测量退化带回关键对比，回答“还有没有工程容错空间”。',
          replay: '回看最差噪声档位的动态抖动过程，辅助判断是否需要更稳健估计链。',
        },
        '任务模式切换': {
          baseline: '先确认模式切换是否整体可行，再看具体切换段峰值和时间线一致性。',
          diagnostics: '重点查切换段失败原因、动态峰值和最差 run，判断是任务设计还是执行链约束。',
          boundary: '把切换结论带回链路，回答是否要继续扩对地任务、执行器边界或 runtime 设计。',
          replay: '直接回到姿态回放和时间线，确认视觉过程是否与结果摘要和 mission 叙事一致。',
        },
        '执行器边界': {
          baseline: '先确认哪一档能力还可接受、哪一档已经逼近边界，再看误差和峰值力矩关系。',
          diagnostics: '重点查饱和、失败原因和最差 run，判断问题更偏参数还是执行器能力不足。',
          boundary: '把关键对比放大，回答“能力不足先体现为误差恶化还是控制边界被触发”。',
          replay: '回看边界工况的动态过程，确认是否已经出现明显迟滞、尖峰或不稳定过渡。',
        },
        '轮速管理': {
          baseline: '先确认动量管理是否改善长期余量，再看是否伤害主姿态任务性能。',
          diagnostics: '重点查轮速趋势、主导扰动和最差 run，判断策略是否过激或收益有限。',
          boundary: '把轮速管理结果带回执行器边界问题，回答是否还需要更强轮组能力。',
          replay: '回到动态过程看轮速回拉与姿态误差是否真正形成正向协同。',
        },
      };
      const copy = themeCopy[theme] || {
        baseline: '先确认这次实验的最佳、最差和边界工况，再决定下一步该读哪类图。',
        diagnostics: '重点查失败原因、动态峰值和最差 run，先把问题解释清楚。',
        boundary: '把关键对比放大，回答差异到底来自参数、环境、任务还是执行器。',
        replay: '回到动态过程核对前面的静态结论，确保结果可解释、可演示。',
      };
      return [
        {
          key: 'baseline',
          order: 1,
          label: '图包 A',
          title: '基线图包',
          purpose: copy.baseline,
          contents: [cards[0]?.title, cards[1]?.title].filter(Boolean),
          useCase: '第一次读结果时先看这一包，先锁定最佳、最差和边界工况。',
          gate: '回答“这次实验整体有没有形成清楚结论”。',
          actionLabel: cards[0]?.actionLabel || '查看基线',
          actionCode: cards[0]?.actionCode || '',
        },
        {
          key: 'diagnostics',
          order: 2,
          label: '图包 B',
          title: '诊断图包',
          purpose: copy.diagnostics,
          contents: ['验收失败原因', '动态峰值摘要', '最差 Run 解释', '主导扰动项'],
          useCase: '当通过率下降、最差 run 异常或结果边界不清楚时，优先切到这一包。',
          gate: '回答“问题主要来自误差、力矩、环境还是任务切换段”。',
          actionLabel: '打开诊断',
          actionCode: "setResultSummaryView('diagnostics')",
        },
        {
          key: 'boundary',
          order: 3,
          label: '图包 C',
          title: '边界图包',
          purpose: copy.boundary,
          contents: [cards[1]?.title, cards[2]?.title, '关键 Run 对比'].filter(Boolean),
          useCase: '当你已经知道哪组配置更好，接下来要判断代价与边界是否值得时，切到这一包。',
          gate: '回答“差异到底来自参数、环境、任务还是执行器能力边界”。',
          actionLabel: compareReady ? '去结果对比' : '查看实验链路',
          actionCode: compareReady ? 'scrollToCompareView()' : "setResultSummaryView('roadmap')",
        },
        {
          key: 'replay',
          order: 4,
          label: '图包 D',
          title: '回放图包',
          purpose: copy.replay,
          contents: replayReady ? ['姿态回放', '任务片段卡片', 'runtime 快照轨迹'] : ['Dashboard 预览', '结果摘要画布'],
          useCase: '做答辩、演示或复核动态过程时，最后进入这一包。',
          gate: '回答“动态过程是否真的支撑前面的静态结论与任务叙事”。',
          actionLabel: replayReady ? '去姿态回放' : '预览 Dashboard',
          actionCode: replayReady ? 'scrollToReplayView()' : 'scrollToPreviewView()',
        },
      ];
    }

    function resultFigureWorkbenchHtml(result) {
      const packages = resultFigurePackages(result);
      const binding = standardAssetBindingRecord({result});
      const primary = packages[0] || null;
      const diagnostics = packages[1] || null;
      const boundary = packages[2] || null;
      const replay = packages[3] || null;
      if (!packages.length) {
        return `
          <div class="history-detail" style="margin-bottom:10px">
            <strong>固定图包工作台</strong>
            <p>当前还没有可用图包，建议先生成完整结果后再读图。</p>
          </div>
        `;
      }
      return `
        <div class="history-detail" style="margin-bottom:10px">
          <strong>固定图包工作台</strong>
          <p>结果页先固定成四包阅读：基线、诊断、边界、回放。这样不同实验共享一致结构，读图时不会因为实验类型不同就完全换一套入口。</p>
          <div class="summary-grid" style="margin-top:10px">
            <div class="summary-card"><span>优先入口</span><strong>${esc(primary ? `${primary.label} ${primary.title}` : '待生成')}</strong></div>
            <div class="summary-card"><span>诊断入口</span><strong>${esc(diagnostics ? `${diagnostics.label} ${diagnostics.title}` : '待生成')}</strong></div>
            <div class="summary-card"><span>边界入口</span><strong>${esc(boundary ? `${boundary.label} ${boundary.title}` : '待生成')}</strong></div>
            <div class="summary-card"><span>回放入口</span><strong>${esc(replay ? `${replay.label} ${replay.title}` : '待生成')}</strong></div>
          </div>
          <div class="flow-sequence" style="margin-top:10px">
            ${packages.map(pkg => `
              <div class="flow-node ${pkg.key === 'baseline' ? 'accent' : pkg.key === 'diagnostics' ? 'warm' : pkg.key === 'replay' ? 'ok' : ''}">
                <small>${esc(`第 ${pkg.order} 包`)}</small>
                <strong>${esc(`${pkg.label} ${pkg.title}`)}</strong>
                <p>${esc(pkg.gate)}</p>
              </div>
            `).join('')}
          </div>
          <div class="detail-grid" style="margin-top:10px">
            ${packages.map(pkg => `
              <div class="detail-box">
                <strong>${esc(`${pkg.label} ${pkg.title}`)}</strong>
                <div>${esc(pkg.useCase)}</div>
                <div class="subtle" style="margin-top:8px">${esc(pkg.gate)}</div>
              </div>
            `).join('')}
          </div>
          ${standardAssetBindingHtml(binding, {title: '图包对应的标准资产绑定'})}
        </div>
      `;
    }

    function resultFigureGuideHtml(result) {
      const cards = resultFigureCards(result);
      const packages = resultFigurePackages(result);
      return `
        <div class="history-detail" style="margin-top:10px; margin-bottom:10px">
          <strong>学术图包导览</strong>
          <p>先按固定图包读结果，再进入具体图卡。这样不同实验会共享统一阅读骨架，但每类实验的重点仍然不同。</p>
          <div class="figure-guide-grid" style="margin-top:10px">
            ${packages.map(pkg => `
              <div class="figure-guide-card">
                <span>${esc(pkg.label)}</span>
                <strong>${esc(pkg.title)}</strong>
                <p>${esc(pkg.purpose)}</p>
                <p>${esc(pkg.useCase)}</p>
                <div class="chips" style="margin-top:8px">${pkg.contents.map(item => `<span class="chip">${esc(item)}</span>`).join('')}</div>
                <div class="subtle" style="margin-top:8px">${esc(pkg.gate)}</div>
                <div class="toolbar" style="margin-top:10px">
                  <button type="button" onclick="${pkg.actionCode || 'void(0)'}" ${pkg.actionCode ? '' : 'disabled'}>${esc(pkg.actionLabel || '查看')}</button>
                </div>
              </div>
            `).join('')}
          </div>
        </div>
        <div class="detail-box" style="margin-top:10px">
          <strong>关键图表阅读面板</strong>
          <div class="subtle" style="margin-top:4px">在固定图包下面，再按当前实验类型给出推荐图和阅读重点，帮助我们用更像论文实验的方式读结果。</div>
          <div class="figure-guide-grid">
            ${cards.map(card => `
              <div class="figure-guide-card">
                <span>${esc(card.label)}</span>
                <strong>${esc(card.title)}</strong>
                <p>${esc(card.purpose)}</p>
                <p>${esc(card.reading)}</p>
                <div class="toolbar" style="margin-top:10px">
                  <button type="button" onclick="${card.actionCode || 'void(0)'}" ${card.actionCode ? '' : 'disabled'}>${esc(card.actionLabel || '查看')}</button>
                </div>
              </div>
            `).join('')}
          </div>
        </div>
      `;
    }

    function resultPublicationCards(result) {
      const packages = resultFigurePackages(result);
      const directCurrentId = findCuratedExperimentIdForResult(result);
      const protocol = experimentProtocolConfig(protocolIdForExperiment(directCurrentId));
      const representativePkg = packages[0];
      const paperPkg = packages[2] || packages[1] || packages[0];
      const replayPkg = packages[3] || packages[0];
      const reportFigures = protocol?.reportFigures || '基线图、诊断图、链路图';
      return [
        {
          label: '出口 A',
          title: '代表图',
          summary: representativePkg
            ? `${representativePkg.label} ${representativePkg.title}，优先用于快速汇报当前实验最核心的结论。`
            : '当前还没有稳定代表图推荐。',
          contents: representativePkg?.contents || ['最佳 run', '通过率', '边界工况'],
          actionLabel: representativePkg?.actionLabel || '查看图包',
          actionCode: representativePkg?.actionCode || "setResultSummaryView('figures')",
        },
        {
          label: '出口 B',
          title: '论文图',
          summary: `${reportFigures}。适合把这次实验整理成一组更正式的论文式结果叙事。`,
          contents: paperPkg?.contents || ['关键 Run 对比', '验收边界', '主线结论'],
          actionLabel: '查看学术图包',
          actionCode: "setResultSummaryView('figures')",
        },
        {
          label: '出口 C',
          title: '演示图',
          summary: replayPkg
            ? `${replayPkg.label} ${replayPkg.title}，更适合答辩、演示或平台汇报时讲动态过程。`
            : '当前更适合直接预览 Dashboard 或姿态回放。',
          contents: replayPkg?.contents || ['姿态回放', '任务片段卡片', 'Dashboard 预览'],
          actionLabel: replayPkg?.actionLabel || '查看演示图包',
          actionCode: replayPkg?.actionCode || 'scrollToPreviewView()',
        },
      ];
    }

    function resultSummaryViewNote(view) {
      if (view === 'diagnostics') {
        return '这里集中看验收失败原因、动态峰值、主导扰动项和最差 run 解释，适合做诊断和复查。';
      }
      if (view === 'figures') {
        return '这里先给固定学术图包，再给当前实验类型的具体图卡和跳转入口，更适合按论文实验的方式组织结果解读。';
      }
      if (view === 'roadmap') {
        return '这里把当前结果放回标准实验主线里，帮助我们判断下一步应继续做哪类实验。';
      }
      if (view === 'artifacts') {
        return '这里汇总参数列、指标列和结果文件入口，适合归档、分享或继续做二次分析。';
      }
      return '这里先看实验摘要、关键结论和当前最重要的判断，是最适合做汇报或快速浏览的一层。';
    }

    function resultWorkbenchViewNote(view) {
      if (view === 'route') {
        return '这里固定结果阅读顺序，先判定，再读图，再回到链路和归档，避免每次都从一堆标签里自己找入口。';
      }
      if (view === 'actions') {
        return '这里集中摆放当前结果最短操作路径，适合决定是回计划、去对比、进回放，还是直接做归档展示。';
      }
      return '默认先看判定焦点，把结论、风险和下一步压成一屏，再进入下方更细的诊断和图表标签。';
    }

    function resultDetailViewNote(view) {
      if (view === 'runs') {
        return '这里把多个 run 压成统一排行，适合先挑最佳、最差或关键 run，再送去对比和回放。';
      }
      if (view === 'detail') {
        return '这里集中看单个 run 的参数、验收和产物，不再让明细跟在结果页最底部长滚动出现。';
      }
      return '默认先看运行概况，把最佳/最差、通过情况和下一步动作压成一屏，再决定是否进入排行或单 run 明细。';
    }

    function compareViewModeNote(view) {
      if (view === 'charts') {
        return '这里集中看姿态误差和力矩曲线。';
      }
      if (view === 'runs') {
        return '这里集中看 Run A / Run B 的指标卡和参数差异。';
      }
      return '默认先看判读台，先锁定研究问题、当前结论和下一步动作。';
    }

    function compareWorkbenchViewNote(view) {
      if (view === 'params') {
        return '这里先看参数差异、验收差异和关键指标。';
      }
      if (view === 'replay') {
        return '这里先看推荐回放对象和继续诊断对象。';
      }
      return '默认先看结论判断，再决定进曲线、指标卡还是回放。';
    }

    function replayViewModeNote(view) {
      if (view === 'mission') {
        return '这里集中看任务模式时间线和片段卡片。';
      }
      if (view === 'runtime') {
        return '这里集中看 runtime 快照、当前 task 和模块顺序。';
      }
      return '默认先看动画工作台，先确认当前回放对象和动态结论。';
    }

    function replayWorkbenchViewNote(view) {
      if (view === 'mission') {
        return '这里先看当前模式片段和切换段，再进入时间线细读。';
      }
      if (view === 'runtime') {
        return '这里先看当前 task、快照数量和调度入口。';
      }
      return '默认先看代表回放，再决定是否进入任务片段或调度快照。';
    }

    function previewViewModeNote(view) {
      if (view === 'frame') {
        return '这里直接内嵌 dashboard.html，适合做平台内快速读图、演示或汇报前复查。';
      }
      if (view === 'archive') {
        return '这里集中看归档文件、结果索引和对外展示出口，适合沉淀实验资产。';
      }
      return '默认先看预览任务书，先判断当前结果适合拿来做什么，再决定是否进入 dashboard 或归档。';
    }

    function resultReadingWorkbenchNodes(result) {
      const attention = Number(result.failed_count || 0) > 0;
      const compareReady = Array.isArray(result.compare_run_ids) && result.compare_run_ids.length > 1;
      return [
        {
          tone: 'accent',
          step: '1. 先判定',
          title: '摘要与工程判断',
          body: attention
            ? '先确认通过率、失败原因和最差 run，再决定是否继续往下读图。'
            : '先确认研究问题、通过率、最佳 run 和工程判断。',
          action: 'overview',
          label: '看摘要',
        },
        {
          tone: attention ? 'warm' : 'ok',
          step: '2. 再诊断 / 读图',
          title: compareReady ? '诊断与图包入口' : '图包与回放入口',
          body: compareReady
            ? '通过诊断、关键对比和固定图包确认差异来自参数、环境还是任务。'
            : '当前对比维度较少，优先看图包、回放和 dashboard 预览。',
          action: attention ? 'diagnostics' : 'figures',
          label: attention ? '去诊断' : '看图包',
        },
        {
          tone: 'ok',
          step: '3. 最后归档主线',
          title: '链路与产物',
          body: '把当前结果放回标准实验主线，并确认哪些文件适合归档、分享或继续二次分析。',
          action: 'roadmap',
          label: '看链路',
        },
      ];
    }

    function resultArtifactGroups(result) {
      const files = Array.isArray(result?.files) ? result.files : [];
      const groups = [
        {
          id: 'reports',
          title: '报告与展示',
          description: '适合直接阅读、汇报或对外展示的页面与摘要。',
          names: ['README.md', 'REPORT_SUMMARY.md', 'PAPER_SUMMARY.md', 'dashboard.html'],
          fallback: file => /\\.(md|html)$/i.test(file.name || ''),
        },
        {
          id: 'machine',
          title: '机器读物与索引',
          description: '适合脚本读取、平台索引和批量分析的标准文件。',
          names: ['index.json', 'experiment_manifest.json', 'study_manifest.json', 'summary_metrics.csv'],
          fallback: file => /\\.(json|csv)$/i.test(file.name || '') && !/runtime_schedule|mode_timeline/i.test(file.name || ''),
        },
        {
          id: 'timeline',
          title: '调度与回放辅助',
          description: '适合任务时间线、调度语义和回放联动复查。',
          names: ['runtime_schedule.json', 'mode_timeline.json'],
          fallback: file => /runtime_schedule|mode_timeline/i.test(file.name || ''),
        },
      ];
      const matched = groups.map(group => {
        const explicit = files.filter(file => group.names.includes(file.name));
        const fallback = files.filter(file => !explicit.includes(file) && group.fallback(file));
        const unique = [];
        [...explicit, ...fallback].forEach(file => {
          if (!unique.some(item => item.name === file.name && item.url === file.url)) {
            unique.push(file);
          }
        });
        return {...group, files: unique};
      });
      const used = new Set(matched.flatMap(group => group.files.map(file => `${file.name}::${file.url}`)));
      const others = files.filter(file => !used.has(`${file.name}::${file.url}`));
      if (others.length) {
        matched.push({
          id: 'others',
          title: '其他产物',
          description: '当前没有归入固定分组的补充文件。',
          files: others,
        });
      }
      return matched;
    }

    function previewReadingWorkbenchNodes(result, matchedPlan) {
      const artifactGroups = resultArtifactGroups(result);
      const reportCount = artifactGroups.find(group => group.id === 'reports')?.files.length || 0;
      const archiveCount = artifactGroups.reduce((sum, group) => sum + group.files.length, 0);
      return [
        {
          tone: 'accent',
          step: '1. 先定用途',
          title: result.description || '先判断这份结果更适合做演示、归档还是进一步诊断。',
          body: `当前结果包含 ${fmt(result.run_count)} 个 run，最佳 run 为 ${result.best_run_id || '—'}${matchedPlan ? `，对应计划为 ${matchedPlan.name}` : ''}。`,
          action: 'brief',
          label: '看任务书',
        },
        {
          tone: reportCount ? 'ok' : 'warm',
          step: '2. 再看界面',
          title: reportCount ? '进入 Dashboard 读图' : '当前预览文件有限',
          body: reportCount
            ? `当前已有 ${reportCount} 个展示型文件，适合直接在内嵌 dashboard 里查看图表、动画和结果摘要。`
            : '当前结果的展示型文件较少，更适合先回到结果摘要或产物页确认输出完整性。',
          action: 'frame',
          label: '看 Dashboard',
        },
        {
          tone: archiveCount ? 'accent' : 'warm',
          step: '3. 最后归档',
          title: archiveCount ? '确认归档与分享出口' : '归档出口待补齐',
          body: archiveCount
            ? `当前可见 ${archiveCount} 个标准产物文件，适合确认哪些文件用于报告、索引和复现实验。`
            : '当前结果目录的标准产物较少，后续应先补齐 summary、manifest 和索引文件。',
          action: 'archive',
          label: '看归档',
        },
      ];
    }

    function setPreviewFrameVisibility(showFrame) {
      if (previewFrame) previewFrame.hidden = !showFrame;
      if (previewEmpty) previewEmpty.hidden = Boolean(state.currentDashboardData);
    }

    function renderPreviewWorkbench(result) {
      if (!previewWorkbench) return;
      const matchedPlan = [...(state.workspace?.experiments || [])].find(plan => matchingDashboardForPlan(plan)?.path === result.path);
      const previewViewMode = ['brief', 'frame', 'archive'].includes(state.previewViewMode) ? state.previewViewMode : 'brief';
      state.previewViewMode = previewViewMode;
      const readingNodes = previewReadingWorkbenchNodes(result, matchedPlan);
      const artifactGroups = resultArtifactGroups(result).filter(group => group.files.length);
      const recommendedFiles = artifactGroups.slice(0, 3).map(group => `${group.title} ${group.files.length} 项`).join(' / ') || '当前没有可归档文件';
      previewWorkbench.hidden = false;
      previewWorkbench.innerHTML = `
        <div class="editor-view-shell" style="margin-bottom:12px">
          <div class="segment-control">
            <button class="${previewViewMode === 'brief' ? 'active' : ''}" type="button" onclick="switchPreviewViewMode('brief')">摘要</button>
            <button class="${previewViewMode === 'frame' ? 'active' : ''}" type="button" onclick="switchPreviewViewMode('frame')">内嵌预览</button>
            <button class="${previewViewMode === 'archive' ? 'active' : ''}" type="button" onclick="switchPreviewViewMode('archive')">归档出口</button>
          </div>
          <div class="editor-view-note">${esc(previewViewModeNote(previewViewMode))}</div>
        </div>
        <div class="history-detail" style="margin-bottom:12px">
          <strong>结果预览任务书</strong>
          <p>这里不是单纯嵌一个 iframe，而是把“为什么看这份 dashboard、先看什么、看完后怎么归档”放成一张预览工作台。</p>
          <div class="flow-sequence" style="margin-top:10px">
            ${readingNodes.map(node => `
              <div class="flow-node ${esc(node.tone)}">
                <small>${esc(node.step)}</small>
                <strong>${esc(node.title)}</strong>
                <p>${esc(node.body)}</p>
                <div class="toolbar" style="margin-top:10px">
                  <button type="button" onclick="switchPreviewViewMode('${esc(node.action)}')">${esc(node.label)}</button>
                </div>
              </div>
            `).join('')}
          </div>
        </div>
        <div class="workspace-panel-view" data-preview-view="brief" ${previewViewMode === 'brief' ? '' : 'hidden'}>
          <div class="summary-grid">
            <div class="summary-card"><span>实验名称</span><strong>${esc(result.experiment_name || result.name)}</strong></div>
            <div class="summary-card"><span>所属场景</span><strong>${esc(result.scenario_name || '—')}</strong></div>
            <div class="summary-card"><span>Run 数量</span><strong>${fmt(result.run_count)}</strong></div>
            <div class="summary-card"><span>最佳 Run</span><strong>${esc(result.best_run_id || '—')}</strong></div>
            <div class="summary-card"><span>通过率</span><strong>${Math.round((result.acceptance_rate || 0) * 1000) / 10}%</strong></div>
            <div class="summary-card"><span>归档文件</span><strong>${(result.files || []).length}</strong></div>
          </div>
          <div class="detail-grid" style="margin-top:10px">
            <div class="detail-box"><strong>预览定位</strong><div>${esc(result.description || '当前结果适合用来快速读图、汇报和归档。')}</div></div>
            <div class="detail-box"><strong>联动计划</strong><div>${matchedPlan ? `${esc(matchedPlan.name)} · ${esc(matchedPlan.path)}` : '当前没有直接匹配的实验计划。'}</div></div>
            <div class="detail-box"><strong>推荐文件组</strong><div>${esc(recommendedFiles)}</div></div>
          </div>
          <div class="toolbar" style="margin-top:10px">
            <button type="button" onclick="setResultSummaryView('overview')">回到结果摘要</button>
            <button class="secondary" type="button" onclick="scrollToCompareView()">去结果对比</button>
            <button class="secondary" type="button" onclick="scrollToReplayView()">去姿态回放</button>
            <button class="secondary" type="button" onclick="${matchedPlan ? `showExperiment('${esc(matchedPlan.path)}')` : 'void(0)'}" ${matchedPlan ? '' : 'disabled'}>查看对应计划</button>
          </div>
        </div>
        <div class="workspace-panel-view" data-preview-view="frame" ${previewViewMode === 'frame' ? '' : 'hidden'}>
          <div class="detail-grid" style="margin-bottom:10px">
            <div class="detail-box"><strong>用途</strong><div>适合直接做学术汇报、结果复核和对外展示。</div></div>
            <div class="detail-box"><strong>读图建议</strong><div>先读 dashboard 摘要，再回到结果摘要页核对最佳 run、对比和回放结论是否一致。</div></div>
          </div>
        </div>
        <div class="workspace-panel-view" data-preview-view="archive" ${previewViewMode === 'archive' ? '' : 'hidden'}>
          <div class="history-detail" style="margin-bottom:10px">
            <strong>归档与分享出口</strong>
            <p>这里集中看哪些文件适合人读、哪些适合脚本读取，以及哪些是 mission / runtime 联动辅助文件。</p>
          </div>
          <div class="figure-guide-grid">
            ${artifactGroups.length ? artifactGroups.map(group => `
              <div class="figure-guide-card">
                <span>${esc(group.title)}</span>
                <strong>${esc(group.files.length)} 个文件</strong>
                <p>${esc(group.description)}</p>
                <div class="files">${group.files.map(file => `<a href="${file.url}" target="_blank">${esc(file.name)}</a>`).join('')}</div>
              </div>
            `).join('') : `
              <div class="figure-guide-card">
                <span>当前状态</span>
                <strong>暂无标准产物</strong>
                <p>当前结果目录还没有足够的标准文件，后续应先补齐 README、索引与摘要产物。</p>
              </div>
            `}
          </div>
        </div>
      `;
      setPreviewFrameVisibility(previewViewMode === 'frame');
    }

    function setResultSummaryView(view) {
      state.resultSummaryView = view || 'overview';
      if (state.currentDashboardData) {
        renderDashboardSummary(state.currentDashboardData);
      }
      syncRouteHash();
    }

    function setResultWorkbenchView(view) {
      state.resultWorkbenchView = view || 'focus';
      if (state.currentDashboardData) {
        renderDashboardSummary(state.currentDashboardData);
      }
      syncRouteHash();
    }

    function setResultDetailView(view) {
      state.resultDetailView = view || 'summary';
      if (state.currentDashboardData) {
        renderDashboardSummary(state.currentDashboardData);
      }
      syncRouteHash();
    }

    function switchCompareViewMode(view) {
      state.compareViewMode = view || 'brief';
      if (state.currentDashboardData) {
        renderCompareView(state.currentDashboardData);
      }
      syncRouteHash();
    }

    function setCompareWorkbenchView(view) {
      state.compareWorkbenchView = view || 'focus';
      if (state.currentDashboardData) {
        renderCompareView(state.currentDashboardData);
      }
      syncRouteHash();
    }

    function switchReplayViewMode(view) {
      state.replayViewMode = view || 'animation';
      if (state.currentDashboardData) {
        renderReplayView(state.currentDashboardData);
      }
      syncRouteHash();
    }

    function setReplayWorkbenchView(view) {
      state.replayWorkbenchView = view || 'focus';
      if (state.currentDashboardData) {
        renderReplayView(state.currentDashboardData);
      }
      syncRouteHash();
    }

    function switchPreviewViewMode(view) {
      state.previewViewMode = view || 'brief';
      if (state.currentDashboardData) {
        renderPreviewWorkbench(state.currentDashboardData);
      }
      syncRouteHash();
    }

    function setDashboardLineFilter(line) {
      state.dashboardLineFilter = line || 'all';
      if (dashboardLineFilter) {
        dashboardLineFilter.value = state.dashboardLineFilter;
      }
      if (state.workspace?.dashboards) {
        renderDashboards();
      }
    }

    function setResultsBrowserView(view) {
      state.resultsBrowserView = view || 'representative';
      if (state.workspace?.dashboards) {
        renderDashboards();
      }
      syncRouteHash();
    }

    function setOverviewWorkbenchView(view) {
      state.overviewWorkbenchView = view || 'position';
      if (state.workspace) {
        renderWorkspaceOverview();
      }
      syncRouteHash();
    }

    function latestAlertStripHtml(latest) {
      const pills = [];
      const failedCount = Number(latest?.failed_count || 0);
      const acceptedCount = Number(latest?.accepted_count || 0);
      const rate = Number(latest?.acceptance_rate || 0);
      pills.push(`<span class="alert-pill ${rate >= 0.999 ? 'good' : rate >= 0.8 ? 'warn' : 'bad'}">通过率 ${Math.round(rate * 1000) / 10}%</span>`);
      pills.push(`<span class="alert-pill ${failedCount ? 'bad' : 'good'}">失败 ${fmt(failedCount)} 个 run</span>`);
      pills.push(`<span class="alert-pill ${acceptedCount ? 'good' : 'warn'}">通过 ${fmt(acceptedCount)} 个 run</span>`);
      if (latest?.best_final_error_deg !== undefined && latest?.best_final_error_deg !== null) {
        pills.push(`<span class="alert-pill ${Number(latest.best_final_error_deg) <= 1 ? 'good' : Number(latest.best_final_error_deg) <= 5 ? 'warn' : 'bad'}">最佳误差 ${fmt(latest.best_final_error_deg)} deg</span>`);
      }
      if (latest?.worst_final_error_deg !== undefined && latest?.worst_final_error_deg !== null) {
        pills.push(`<span class="alert-pill ${Number(latest.worst_final_error_deg) <= 5 ? 'good' : Number(latest.worst_final_error_deg) <= 15 ? 'warn' : 'bad'}">最差误差 ${fmt(latest.worst_final_error_deg)} deg</span>`);
      }
      return `<div class="alert-strip">${pills.join('')}</div>`;
    }

    function renderRunStatusPanels() {
      const action = state.runAction;
      const latest = state.latestRunSummary;
      if (action) {
        ensureRunProgressTimer();
        const snapshot = stageSnapshot(action);
        runProgressPanel.innerHTML = `
          <div class="callout info busy" style="margin-bottom:0">
            <strong>${esc(action.mode === 'rerun' ? '正在重新运行实验计划' : '正在运行实验计划')}</strong>
            <p>平台正在执行 ${esc(action.planName || action.path || '当前实验')}，输出目录为 ${esc(action.outputDir || '默认结果目录')}。完成后会自动刷新结果浏览、当前结果摘要和最新运行卡片。</p>
            <div class="progress-track"><div class="progress-fill" style="width:${Math.max(8, snapshot.progress * 100)}%"></div></div>
            <div class="progress-meta">
              <span>当前阶段：${esc(snapshot.stages[snapshot.activeIndex])}</span>
              <span>已运行约 ${fmt(snapshot.elapsedS)} s</span>
            </div>
            <div class="stage-list">
              ${snapshot.stages.map((stage, index) => {
                const cls = index < snapshot.activeIndex ? 'stage-chip done' : index === snapshot.activeIndex ? 'stage-chip active' : 'stage-chip';
                return `<span class="${cls}">${esc(stage)}</span>`;
              }).join('')}
            </div>
          </div>
        `;
      } else {
        stopRunProgressTimer();
        runProgressPanel.innerHTML = '<div class="empty">点击“运行计划”或“创建并运行”后，这里会显示当前执行状态。</div>';
      }

      if (latest) {
        const matchedPlan = [...(state.workspace?.experiments || [])].find(plan => matchingDashboardForPlan(plan)?.path === latest.path);
        const rate = `${Math.round((latest.acceptance_rate || 0) * 1000) / 10}%`;
        latestRunSummaryPanel.innerHTML = `
          <div class="result-banner" style="margin-bottom:0">
            <span>最近一次运行摘要</span>
            <strong>${esc(latest.experiment_name || latest.name)} · 场景 ${esc(latest.scenario_name || latest.scenario || '—')} · run ${esc(latest.run_count)} · 通过率 ${esc(rate)}</strong>
            <p style="margin:8px 0 0;color:var(--muted);font-size:12px;line-height:1.5">
              最佳 run 为 ${esc(latest.best_run_id || '—')}，最佳末端误差 ${fmt(latest.best_final_error_deg)} deg。
              ${latest.updated_at ? ` 最近更新时间 ${esc(latest.updated_at)}。` : ''}
            </p>
            ${latestAlertStripHtml(latest)}
            <div class="toolbar" style="margin-top:10px">
              <button type="button" onclick="showDashboard('${esc(latest.path)}')">查看最新结果</button>
              <button class="secondary" type="button" onclick="window.open('${esc(latest.url)}', '_blank')">新窗口打开</button>
              <button class="secondary" type="button" onclick="${matchedPlan ? `showExperiment('${esc(matchedPlan.path)}')` : 'void(0)'}" ${matchedPlan ? '' : 'disabled'}>查看对应计划</button>
            </div>
            ${latestMetricOverviewHtml(latest)}
            ${resultGuideHtml(latest, {latest: true})}
            ${recentDashboardTrendHtml(latest.path)}
          </div>
        `;
      } else {
        latestRunSummaryPanel.innerHTML = '<div class="empty">最近一次运行完成后，这里会固定显示结果摘要和快捷入口。</div>';
      }
    }

    function filteredExperiments() {
      const rows = [...(state.workspace?.experiments || [])].sort((a, b) => Number(b.updated_ts || 0) - Number(a.updated_ts || 0));
      const filterText = state.experimentFilter.trim().toLowerCase();
      return rows.filter(plan => {
        const textOk = !filterText || `${plan.name || ''} ${plan.scenario || ''} ${plan.path || ''}`.toLowerCase().includes(filterText);
        if (!textOk) return false;
        const dashboard = matchingDashboardForPlan(plan);
        if (state.experimentStatusFilter === 'with_results') return Boolean(dashboard);
        if (state.experimentStatusFilter === 'without_results') return !dashboard;
        if (state.experimentStatusFilter === 'current') return plan.path === state.currentExperiment;
        return true;
      });
    }

    function selectedExperimentRows() {
      const selected = new Set(state.selectedExperiments || []);
      return filteredExperiments().filter(plan => selected.has(plan.path));
    }

    function toggleExperimentSelection(path, checked) {
      const selected = new Set(state.selectedExperiments || []);
      if (checked) {
        selected.add(path);
      } else {
        selected.delete(path);
      }
      state.selectedExperiments = [...selected];
      renderExperimentBatchBar();
      renderExperiments();
    }

    function selectAllFilteredExperiments() {
      state.selectedExperiments = filteredExperiments().map(plan => plan.path);
      renderExperimentBatchBar();
      renderExperiments();
    }

    function clearSelectedExperiments() {
      state.selectedExperiments = [];
      renderExperimentBatchBar();
      renderExperiments();
    }

    function renderExperimentBatchBar() {
      const filtered = filteredExperiments();
      const selectedRows = selectedExperimentRows();
      const selectedCount = selectedRows.length;
      const allSelected = filtered.length > 0 && selectedCount === filtered.length;
      experimentBatchBar.innerHTML = `
        <div class="history-detail" style="margin-bottom:0">
          <strong>批量操作入口</strong>
          <p>当前筛选结果 ${filtered.length} 个计划，已选择 ${selectedCount} 个。可以用它做集中校验或集中归档，避免逐条点选。</p>
          <div class="toolbar" style="margin-top:10px">
            <button type="button" onclick="${allSelected ? 'clearSelectedExperiments()' : 'selectAllFilteredExperiments()'}" ${filtered.length ? '' : 'disabled'}>${allSelected ? '清空选择' : '全选当前筛选结果'}</button>
            <button class="secondary" type="button" onclick="clearSelectedExperiments()" ${selectedCount ? '' : 'disabled'}>取消已选</button>
            <button class="secondary" type="button" onclick="batchValidateExperiments()" ${selectedCount ? '' : 'disabled'}>批量校验</button>
            <button class="secondary" type="button" onclick="batchArchiveExperiments()" ${selectedCount ? '' : 'disabled'}>批量归档</button>
          </div>
        </div>
      `;
    }

    function normalizedAssetName(value) {
      const text = String(value || '').trim().replace(/\\\\/g, '/');
      if (!text) return '';
      const leaf = text.split('/').pop() || '';
      return leaf.replace(/\\.(json|html)$/i, '').toLowerCase();
    }

    function dashboardFolderName(item) {
      const path = String(item?.path || '').trim().replace(/\\\\/g, '/');
      const parts = path.split('/').filter(Boolean);
      if (parts.length >= 2) return normalizedAssetName(parts[parts.length - 2]);
      return normalizedAssetName(item?.name || '');
    }

    function planMatchKeys(plan) {
      const keys = new Set();
      const push = value => {
        const normalized = normalizedAssetName(value);
        if (normalized) keys.add(normalized);
      };
      push(plan?.name);
      push(plan?.asset_family);
      push(plan?.path);
      return [...keys];
    }

    function dashboardPlanMatchScore(plan, dashboard) {
      if (!plan || !dashboard) return -1;
      const name = normalizedAssetName(dashboard?.name || '');
      const folder = dashboardFolderName(dashboard);
      const path = String(dashboard?.path || '').toLowerCase().replace(/\\\\/g, '/');
      const scenario = String(dashboard?.scenario || '').trim().toLowerCase();
      const planScenario = String(plan?.scenario || '').trim().toLowerCase();
      let score = 0;
      for (const key of planMatchKeys(plan)) {
        if (!key) continue;
        if (name === key) score = Math.max(score, 140);
        if (folder === key) score = Math.max(score, 135);
        if (path.includes(`/${key}/dashboard.html`)) score = Math.max(score, 130);
        if (path.includes(`/${key}_`)) score = Math.max(score, 118);
        if (name.startsWith(`${key}_`) || folder.startsWith(`${key}_`)) score = Math.max(score, 112);
        if (name.includes(key) || folder.includes(key)) score = Math.max(score, 96);
      }
      if (planScenario && scenario && planScenario === scenario) score += 8;
      if (plan?.path === state.currentExperiment) score += 2;
      return score;
    }

    function matchingDashboardsForPlan(plan, dashboards = null) {
      if (!plan) return [];
      const rows = [...(dashboards || state.workspace?.dashboards || [])]
        .map(item => ({item, score: dashboardPlanMatchScore(plan, item)}))
        .filter(entry => entry.score > 0)
        .sort((left, right) =>
          right.score - left.score
          || Number(right.item.updated_ts || 0) - Number(left.item.updated_ts || 0)
          || Number(right.item.acceptance_rate || 0) - Number(left.item.acceptance_rate || 0)
        );
      return rows.map(entry => entry.item);
    }

    function matchingDashboardForPlan(plan) {
      return matchingDashboardsForPlan(plan)[0] || null;
    }

    function planAssetProfile(plan) {
      if (!plan) {
        return {
          line: '—',
          status: '未选择',
          question: '还没有载入实验计划。',
          next: '先从实验池或实验库中选择一个计划。',
          curated: false,
          curatedId: '',
          config: null,
        };
      }
      const curatedId = findCuratedExperimentIdByPlanPath(plan.path);
      const config = curatedExperimentConfig(curatedId);
      const detailMeta = experimentLibraryDetailMeta(curatedId, config);
      const assetLabel = plan.asset_label || '';
      const assetKind = plan.asset_kind || '';
      return {
        line: config?.theme || '工作区派生计划',
        status: config
          ? '实验库标准资产'
          : (assetLabel || (plan.path === state.builderLastCreated?.path ? '刚创建计划' : '工作区计划')),
        question: config?.question || config?.description || plan.description || detailMeta.platform || '当前计划用于组织一组可复现实验。',
        next: plan.needs_consolidation
          ? `当前资产族“${plan.asset_family || plan.name}”存在多个副本，建议保留代表计划后把其余副本归档，再继续沿 ${config?.label || '当前实验'} 主线派生。`
          : (detailMeta.next || '建议继续补结果、校验或派生新的实验变体。'),
        curated: Boolean(config),
        curatedId,
        config,
        assetKind,
        assetLabel,
        needsConsolidation: Boolean(plan.needs_consolidation),
        assetFamily: plan.asset_family || plan.name,
      };
    }

    function planLifecycleStage(plan) {
      if (!plan) {
        return {
          id: 'empty',
          label: '未选择',
          note: '先从实验池或实验库中选择一个计划。',
        };
      }
      const dashboard = matchingDashboardForPlan(plan);
      const profile = planAssetProfile(plan);
      if (plan.error) {
        return {
          id: 'needs_fix',
          label: '需修正',
          note: '计划文件存在结构、路径或配置问题，适合先修正后再运行。',
        };
      }
      if (dashboard) {
        return {
          id: 'with_results',
          label: '已形成结果',
          note: '已经生成标准实验目录，适合继续读图、对比和派生变体。',
        };
      }
      if (plan.path === state.currentExperiment) {
        return {
          id: 'editing',
          label: '正在编辑',
          note: '当前计划已载入编辑器，适合补任务、变量或验收细节。',
        };
      }
      if (profile.curated) {
        return {
          id: 'template_ready',
          label: '标准模板待运行',
          note: '来自实验库的标准资产，适合先形成一版代表结果。',
        };
      }
      return {
        id: 'workspace_ready',
        label: '派生计划待运行',
        note: '工作区派生计划已经具备基本结构，下一步适合校验并执行首轮实验。',
      };
    }

    function planResearchMeta(plan) {
      const profile = planAssetProfile(plan);
      const currentId = profile.curatedId || findCuratedExperimentIdByPlanPath(plan?.path || '');
      const config = currentId ? curatedExperimentConfig(currentId) : profile.config;
      const academic = experimentAcademicMeta(currentId, config);
      return {
        profile,
        currentId,
        config,
        detail: experimentLibraryDetailMeta(currentId, config),
        academic,
        standard: experimentStandardCardMeta(currentId, config, academic),
        roadmap: experimentRoadmapProfile(currentId, profile.line),
      };
    }

    function workspaceLineCoverageRows(plans) {
      const grouped = new Map();
      for (const plan of plans || []) {
        const meta = planResearchMeta(plan);
        const key = meta.profile.line || '工作区派生计划';
        if (!grouped.has(key)) {
          grouped.set(key, {
            line: key,
            count: 0,
            withResults: 0,
            current: false,
            representative: plan,
            curatedId: meta.profile.curatedId || '',
            question: meta.profile.question || '当前主线用于组织一组可复现实验。',
          });
        }
        const row = grouped.get(key);
        row.count += 1;
        row.withResults += matchingDashboardForPlan(plan) ? 1 : 0;
        row.current = row.current || plan.path === state.currentExperiment;
        if (plan.path === state.currentExperiment) {
          row.representative = plan;
          row.curatedId = meta.profile.curatedId || row.curatedId;
          row.question = meta.profile.question || row.question;
        }
      }
      return [...grouped.values()].sort((left, right) => right.count - left.count || right.withResults - left.withResults);
    }

    function planSweepSummaryRows(mapping) {
      const sweeps = Array.isArray(mapping?.sweeps) ? mapping.sweeps : [];
      const monteCarlo = mapping?.monte_carlo || {};
      const rows = sweeps.map((item, index) => {
        const values = Array.isArray(item?.values) ? item.values : [];
        return {
          name: `扫描 ${index + 1}`,
          path: item?.path || '未指定路径',
          label: parameterColumnLabel(item?.path || ''),
          count: values.length,
          values: values.map(value => String(value)),
        };
      });
      if (Number(monteCarlo.samples || 0) > 0) {
        rows.push({
          name: 'Monte Carlo',
          path: 'monte_carlo.sample',
          label: 'Monte Carlo 样本',
          count: Number(monteCarlo.samples || 0),
          values: [monteCarlo.seed !== undefined && monteCarlo.seed !== null && monteCarlo.seed !== '' ? `seed ${monteCarlo.seed}` : 'seed 未指定'],
        });
      }
      return rows;
    }

    function acceptanceProfileRows(mapping) {
      const acceptance = mapping?.acceptance || {};
      return [
        acceptance.max_final_error_deg !== undefined ? {label: '末端误差', value: `<= ${fmt(acceptance.max_final_error_deg)} deg`} : null,
        acceptance.max_rms_error_deg !== undefined ? {label: 'RMS 误差', value: `<= ${fmt(acceptance.max_rms_error_deg)} deg`} : null,
        acceptance.max_peak_torque_nm !== undefined ? {label: '峰值力矩', value: `<= ${fmt(acceptance.max_peak_torque_nm)} Nm`} : null,
      ].filter(Boolean);
    }

    function planReadinessText(mapping, dashboard) {
      const sweepRows = planSweepSummaryRows(mapping);
      const acceptanceRows = acceptanceProfileRows(mapping);
      if (dashboard) {
        return '当前计划已经形成结果闭环，适合继续做图表阅读、关键 run 对比和下一组派生实验。';
      }
      if (!sweepRows.length && !acceptanceRows.length) {
        return '当前计划仍偏基础，建议先补扫描变量或验收口径，再执行首轮实验。';
      }
      if (!acceptanceRows.length) {
        return '变量设计已经具备，但验收口径还偏弱，建议补上通过标准后再运行。';
      }
      return '当前计划已经具备变量和验收骨架，下一步适合执行首轮实验并生成标准结果目录。';
    }

    function resultLineForDashboard(result) {
      const matchedPlan = [...(state.workspace?.experiments || [])].find(plan => matchingDashboardForPlan(plan)?.path === result?.path);
      return matchedPlan ? planAssetProfile(matchedPlan).line : resultThemeLabel(result);
    }

    function groupedDashboardLines(dashboards) {
      const counts = new Map();
      for (const item of dashboards || []) {
        const line = resultLineForDashboard(item) || '结果工作区';
        counts.set(line, (counts.get(line) || 0) + 1);
      }
      return [...counts.entries()].sort((left, right) => right[1] - left[1]);
    }

    function bestDashboardForLine(line, dashboards = null) {
      const rows = [...(dashboards || state.workspace?.dashboards || [])]
        .filter(item => resultLineForDashboard(item) === line)
        .sort((a, b) =>
          Number(b.acceptance_rate || 0) - Number(a.acceptance_rate || 0)
          || Number(a.best_final_error_deg ?? Number.POSITIVE_INFINITY) - Number(b.best_final_error_deg ?? Number.POSITIVE_INFINITY)
          || Number(b.updated_ts || 0) - Number(a.updated_ts || 0)
        );
      return rows[0] || null;
    }

    function canonicalExperimentIdForLine(line) {
      const target = String(line || '').trim();
      if (!target) return '';
      const routeIds = standardExperimentRouteConfig().map(stage => stage.experimentId);
      const branchIds = supportingBranchRouteConfig().map(stage => stage.experimentId);
      const orderedIds = [...new Set([...routeIds, ...branchIds])];
      return orderedIds.find(id => String(curatedExperimentConfig(id)?.theme || '').trim() === target) || '';
    }

    function representativeDashboardForLine(line, dashboards = null) {
      const canonicalId = canonicalExperimentIdForLine(line);
      if (canonicalId) {
        const canonical = representativeDashboardForExperiment(canonicalId, dashboards);
        if (canonical) return canonical;
      }
      return bestDashboardForLine(line, dashboards);
    }

    function latestDashboardForLine(line, dashboards = null) {
      const rows = [...(dashboards || state.workspace?.dashboards || [])]
        .sort((a, b) => Number(b.updated_ts || 0) - Number(a.updated_ts || 0));
      return rows.find(item => resultLineForDashboard(item) === line) || null;
    }

    function sameDashboardRecord(left, right) {
      return Boolean(left?.path && right?.path && left.path === right.path);
    }

    function lineDashboardAssetMeta(line, dashboards = null) {
      const asset = representativeAssetRecord({line, dashboards});
      const representative = asset.dashboard || null;
      const latest = asset.latestDashboard || latestDashboardForLine(line, dashboards);
      const best = bestDashboardForLine(line, dashboards);
      return {
        asset,
        representative,
        latest,
        best,
        latestIsRepresentative: sameDashboardRecord(latest, representative),
        bestIsRepresentative: sameDashboardRecord(best, representative),
      };
    }

    function isRepresentativeDashboard(result, dashboards = null) {
      if (!result) return false;
      const line = resultLineForDashboard(result);
      if (!line) return false;
      const representative = representativeDashboardForLine(line, dashboards);
      return sameDashboardRecord(result, representative);
    }

    function browserPrimaryDashboard(view, rows, allRows, browserLines) {
      const current = rows.find(item => item.path === state.currentDashboard) || null;
      const latestRun = rows.find(item => item.path === state.latestRunDashboard) || null;
      if (view === 'recent') {
        return current || latestRun || rows[0] || null;
      }
      if (view === 'representative') {
        if (current && isRepresentativeDashboard(current, rows)) {
          return current;
        }
        const firstRepresentative = (browserLines || [])
          .map(([groupName]) => lineDashboardAssetMeta(groupName, rows).representative)
          .find(Boolean);
        return firstRepresentative || rows.find(item => isRepresentativeDashboard(item, rows)) || current || latestRun || rows[0] || null;
      }
      return current || latestRun || rows[0] || allRows[0] || null;
    }

    function representativePlanForLine(line) {
      const canonicalId = canonicalExperimentIdForLine(line);
      if (canonicalId) {
        const config = curatedExperimentConfig(canonicalId);
        const planPath = resolveCuratedExperimentPlan(config);
        if (planPath) {
          const plan = [...(state.workspace?.experiments || [])].find(item => item.path === planPath) || null;
          if (plan) return plan;
        }
      }
      const rows = [...(state.workspace?.experiments || [])]
        .filter(plan => planAssetProfile(plan).line === line)
        .sort((a, b) =>
          Number(Boolean(b.path === state.currentExperiment)) - Number(Boolean(a.path === state.currentExperiment))
          || Number(Boolean(planAssetProfile(b).curated)) - Number(Boolean(planAssetProfile(a).curated))
          || Number(Boolean(matchingDashboardForPlan(b))) - Number(Boolean(matchingDashboardForPlan(a)))
          || Number(b.updated_ts || 0) - Number(a.updated_ts || 0)
        );
      return rows[0] || null;
    }

    function representativeAssetRecord(options = {}) {
      const dashboards = [...(options.dashboards || state.workspace?.dashboards || [])];
      let experimentId = options.experimentId || '';
      let config = experimentId ? curatedExperimentConfig(experimentId) : null;
      let plan = options.plan || null;
      if (!plan && config) {
        const planPath = resolveCuratedExperimentPlan(config);
        if (planPath) {
          plan = [...(state.workspace?.experiments || [])].find(item => item.path === planPath) || null;
        }
      }
      let line = options.line || '';
      if (!line && plan) {
        line = planAssetProfile(plan).line;
      }
      if (!line && config) {
        line = config.theme || '工作区派生计划';
      }
      if (!plan && line) {
        plan = representativePlanForLine(line);
      }
      const profile = planAssetProfile(plan);
      if (!experimentId && profile.curatedId) {
        experimentId = profile.curatedId;
        config = curatedExperimentConfig(experimentId);
      }
      if (!line) {
        line = profile.line || config?.theme || '工作区派生计划';
      }
      const dashboard = experimentId
        ? (representativeDashboardForExperiment(experimentId)
          || matchingDashboardForPlan(plan)
          || bestDashboardForLine(line, dashboards))
        : (matchingDashboardForPlan(plan) || bestDashboardForLine(line, dashboards));
      const latestDashboard = line ? latestDashboardForLine(line, dashboards) : dashboard;
      const academicMeta = experimentAcademicMeta(experimentId, config);
      const standardMeta = experimentStandardCardMeta(
        experimentId,
        config || {planName: plan?.name || line, theme: line},
        academicMeta,
      );
      const status = dashboard
        ? '代表结果已就绪'
        : plan
          ? '代表计划待运行'
          : experimentId
            ? '标准模板待落地'
            : '当前主线待收敛';
      const next = dashboard
        ? '优先打开代表结果做摘要、图表和回放，再决定是否派生下一轮计划。'
        : plan
          ? '先运行代表计划，形成一版稳定结果，再把同族副本收敛掉。'
          : experimentId
            ? '先把当前标准模板载入工作区，固化一份代表计划，再开始派生。'
            : '先在当前主线里保留一个更稳定的计划作为代表资产。';
      return {
        line,
        experimentId,
        config,
        plan,
        dashboard,
        latestDashboard,
        profile,
        academicMeta,
        standardMeta,
        status,
        next,
      };
    }

    function standardAssetBindingRecord(options = {}) {
      let experimentId = options.experimentId || '';
      const plan = options.plan || null;
      const result = options.result || null;
      if (!experimentId && result) {
        experimentId = findCuratedExperimentIdForResult(result);
      }
      if (!experimentId && plan) {
        experimentId = planAssetProfile(plan).curatedId || '';
      }
      const asset = representativeAssetRecord({
        experimentId,
        plan,
        dashboards: options.dashboards,
      });
      const config = asset.config || (experimentId ? curatedExperimentConfig(experimentId) : null);
      const protocolId = experimentId ? protocolIdForExperiment(experimentId) : '';
      const protocol = experimentProtocolConfig(protocolId);
      const scenarioPath = config ? resolveCuratedExperimentScenario(config) : '';
      const planPath = config ? resolveCuratedExperimentPlan(config) : '';
      const dashboard = result || asset.dashboard || null;
      return {
        experimentId,
        config,
        protocolId,
        protocol,
        scenarioPath,
        planPath,
        plan: asset.plan || plan || null,
        dashboard,
        asset,
      };
    }

    function standardAssetFlowNodes(binding) {
      if (!binding) return [];
      const config = binding.config;
      const plan = binding.plan;
      const dashboard = binding.dashboard;
      const protocol = binding.protocol;
      const asset = binding.asset || {};
      return [
        {
          tone: config ? 'accent' : 'warm',
          step: '1. 模板',
          title: config?.label || binding.experimentId || '标准模板待补',
          body: config
            ? `${binding.scenarioPath || '场景待补'} · ${protocol?.label || '协议待补'}`
            : '当前还没有稳定模板绑定，建议先从实验库载入标准实验。',
        },
        {
          tone: plan ? 'accent' : 'warm',
          step: '2. 计划',
          title: plan ? plan.name : '代表计划待固化',
          body: plan
            ? `${experimentScaleText(plan)} · ${plan.path}`
            : '当前主线还没有稳定代表计划，建议先固化一份可复用计划。',
        },
        {
          tone: dashboard ? 'ok' : 'warm',
          step: '3. 结果',
          title: dashboard ? dashboard.name : '代表结果待生成',
          body: dashboard
            ? representativeDashboardSummary(dashboard)
            : '当前还没有稳定代表结果，建议先运行代表计划形成标准结果目录。',
        },
        {
          tone: 'ok',
          step: '4. 下一步',
          title: asset.status || '继续主线推进',
          body: asset.next || '把当前资产链继续沉淀成更稳定的模板、计划和结果入口。',
        },
      ];
    }

    function standardAssetBindingHtml(binding, options = {}) {
      const title = options.title || '标准资产绑定';
      const showToolbar = options.showToolbar !== false;
      if (!binding || (!binding.experimentId && !binding.plan && !binding.dashboard)) {
        return `
          <div class="history-detail" style="margin:10px 0 0">
            <strong>${esc(title)}</strong>
            <p>当前还没有匹配到稳定的标准资产链。建议先从实验库载入一个主实验模板，形成代表计划和代表结果。</p>
          </div>
        `;
      }
      const config = binding.config;
      const plan = binding.plan;
      const dashboard = binding.dashboard;
      const protocol = binding.protocol;
      const asset = binding.asset;
      const flowNodes = standardAssetFlowNodes(binding);
      return `
        <div class="history-detail" style="margin:10px 0 0">
          <strong>${esc(title)}</strong>
          <p>把标准模板、代表计划和代表结果固定成同一条资产链，避免实验库、计划管理和结果页各自维护不同入口。</p>
          <div class="summary-grid" style="margin-top:10px">
            <div class="summary-card"><span>代表模板</span><strong>${esc(config?.label || binding.experimentId || '待补')}</strong></div>
            <div class="summary-card"><span>代表计划</span><strong>${esc(plan ? plan.name : '待固化')}</strong></div>
            <div class="summary-card"><span>代表结果</span><strong>${esc(dashboard ? dashboard.name : '待生成')}</strong></div>
            <div class="summary-card"><span>当前状态</span><strong>${esc(asset.status || '待收敛')}</strong></div>
          </div>
          <div class="flow-sequence" style="margin-top:12px">
            ${flowNodes.map(node => `
              <div class="flow-node ${esc(node.tone)}">
                <small>${esc(node.step)}</small>
                <strong>${esc(node.title)}</strong>
                <p>${esc(node.body)}</p>
              </div>
            `).join('')}
          </div>
          <div class="detail-grid" style="margin-top:10px">
            <div class="detail-box">
              <strong>代表模板</strong>
              <div>${config ? `${esc(config.label || binding.experimentId)} · ${esc(binding.scenarioPath || '场景待补')}` : '当前未匹配到标准模板。'}</div>
            </div>
            <div class="detail-box">
              <strong>代表计划</strong>
              <div>${plan ? `${esc(plan.name)} · ${esc(experimentScaleText(plan))}` : '当前还没有稳定代表计划。'}</div>
            </div>
            <div class="detail-box">
              <strong>代表结果</strong>
              <div>${dashboard ? `${esc(dashboard.name)} · ${esc(representativeDashboardSummary(dashboard))}` : '当前还没有稳定代表结果。'}</div>
            </div>
            <div class="detail-box">
              <strong>协议语义</strong>
              <div>${esc(protocol ? `${protocol.label}：${protocol.summary}` : '当前资产尚未绑定到明确协议。')}</div>
            </div>
            <div class="detail-box">
              <strong>资产状态</strong>
              <div>${esc(asset.status)} · ${esc(asset.standardMeta.assetRole)}</div>
            </div>
            <div class="detail-box">
              <strong>推荐下一步</strong>
              <div>${esc(asset.next)}</div>
            </div>
          </div>
          ${showToolbar ? `
            <div class="toolbar" style="margin-top:10px">
              <button type="button" onclick="${binding.experimentId ? `previewRoadmapExperiment('${binding.experimentId}')` : 'void(0)'}" ${binding.experimentId ? '' : 'disabled'}>在实验库定位</button>
              <button class="secondary" type="button" onclick="${config ? `prepareCuratedExperiment('${binding.experimentId}')` : 'void(0)'}" ${config ? '' : 'disabled'}>载入标准模板</button>
              <button class="secondary" type="button" onclick="${plan ? `showExperiment('${esc(plan.path)}')` : 'void(0)'}" ${plan ? '' : 'disabled'}>打开代表计划</button>
              <button class="secondary" type="button" onclick="${dashboard ? `showDashboard('${esc(dashboard.path)}')` : 'void(0)'}" ${dashboard ? '' : 'disabled'}>查看代表结果</button>
            </div>
          ` : ''}
        </div>
      `;
    }

    function dashboardRiskLabel(item) {
      const rate = Number(item?.acceptance_rate || 0);
      const failed = Number(item?.failed_count || 0);
      if (failed > 0 && rate < 0.6) return '高风险';
      if (failed > 0 || rate < 0.999) return '需关注';
      return '稳定';
    }

    function dashboardRiskReason(item) {
      const rate = Number(item?.acceptance_rate || 0);
      const failed = Number(item?.failed_count || 0);
      if (failed > 0 && rate < 0.6) {
        return `当前失败 ${fmt(failed)} 个 run，通过率只有 ${percentText(rate)}，适合优先做诊断与边界复查。`;
      }
      if (failed > 0) {
        return `当前存在 ${fmt(failed)} 个失败 run，建议先看最差 Run 解释和验收失败原因。`;
      }
      return `当前通过率 ${percentText(rate)}，可以把它当作该主线的稳定代表结果继续做读图与汇报。`;
    }

    function cockpitReadingRoute(result) {
      const theme = resultThemeLabel(result);
      const routes = {
        '控制器整定': [
          '先看 Run 排行，锁定最佳参数点和边界参数点。',
          '再看结果对比里的误差与力矩曲线，判断性能改善是否值得。',
          '最后把最优参数送去 Monte Carlo 或严格验收实验继续验证。',
        ],
        '鲁棒性': [
          '先看通过率和最差 run，判断统计边界是否已经暴露。',
          '再看对比和回放，确认最差工况的动态退化形态。',
          '最后把同一配置带到更真实场景，例如故障或轨道环境。',
        ],
        '任务模式切换': [
          '先看结果摘要和切换段关键指标，确认任务是否大体可行。',
          '再去姿态回放点击任务片段卡片，检查切换段是否平滑。',
          '最后看 runtime 快照轨迹，核对 task、modules 和时间线是否一致。',
        ],
        '执行器边界': [
          '先看失败结果和最差 run，确认是否已经触到执行器边界。',
          '再看结果对比与回放，判断是误差先坏，还是力矩 / 饱和先坏。',
          '最后回到计划管理，把边界实验接到轮速管理或任务实验里继续收敛。',
        ],
      };
      return routes[theme] || [
        '先看结果摘要，确认这次实验主要在回答什么问题。',
        '再看对应图表或对比区，判断关键差异来自参数、环境还是任务流程。',
        '最后沿实验链路继续扩一组更具体的后续实验。',
      ];
    }

    function preferredDashboardRecord() {
      const dashboards = [...(state.workspace?.dashboards || [])].sort((a, b) => Number(b.updated_ts || 0) - Number(a.updated_ts || 0));
      if (!dashboards.length) return null;
      return dashboards.find(item => item.path === state.currentDashboard)
        || dashboards.find(item => item.path === state.latestRunDashboard)
        || dashboards[0];
    }

    function renderManagePoolCockpit() {
      if (!managePoolCockpit) return;
      const allRows = [...(state.workspace?.experiments || [])].sort((a, b) => Number(b.updated_ts || 0) - Number(a.updated_ts || 0));
      if (!allRows.length) {
        if (manageSideSignals) {
          manageSideSignals.innerHTML = `
            <div class="summary-card"><span>焦点计划</span><strong>0</strong></div>
            <div class="summary-card"><span>结果状态</span><strong>暂无</strong></div>
            <div class="summary-card"><span>主线位置</span><strong>未建立</strong></div>
            <div class="summary-card"><span>下一动作</span><strong>先建计划</strong></div>
          `;
        }
        if (manageSideRoute) {
          manageSideRoute.innerHTML = `
            <div class="history-detail" style="margin-bottom:0">
              <strong>主线建议</strong>
              <p>当前还没有实验计划。先从实验库载入一个标准实验，或者去创建实验生成第一份计划。</p>
            </div>
          `;
        }
        managePoolCockpit.innerHTML = `
          <div class="callout">
            <strong>计划资产总览</strong>
            <p>当前还没有实验计划。建议先去“实验库”载入一个成熟模板，或去“创建实验”生成第一份计划。</p>
          </div>
        `;
        return;
      }
      const filtered = filteredExperiments();
      const currentPlan = allRows.find(plan => plan.path === state.currentExperiment) || null;
      const focusPlan = currentPlan || filtered[0] || allRows[0];
      const focusDashboard = matchingDashboardForPlan(focusPlan);
      const withResults = allRows.filter(plan => matchingDashboardForPlan(plan)).length;
      const issuePlans = allRows.filter(plan => plan.error).length;
      const pendingPlans = allRows.filter(plan => !plan.error && !matchingDashboardForPlan(plan)).length;
      const profile = planAssetProfile(focusPlan);
      const lifecycle = planLifecycleStage(focusPlan);
      const routeStages = standardRouteStageRecords();
      const focusExperimentId = findCuratedExperimentIdByPlanPath(focusPlan.path) || profile.curatedId || '';
      const focusStage = routeStages.find(stage => stage.experimentId === focusExperimentId) || null;
      const missingResultStage = routeStages.find(stage => stage.plan && !stage.result) || null;
      const missingPlanStage = routeStages.find(stage => !stage.plan) || null;
      const showcaseStage = routeStages.find(stage => stage.id === 'mission_sequence') || routeStages.find(stage => stage.id === 'actuator_margin') || routeStages[routeStages.length - 1] || null;
      const attentionRows = allRows
        .filter(plan => plan.error || !matchingDashboardForPlan(plan))
        .slice(0, 3);
      const lifecycleRows = [
        {
          label: '标准模板',
          count: allRows.filter(plan => planAssetProfile(plan).curated).length,
          note: '来自实验库的标准计划，可作为平台代表实验或研究起点。',
        },
        {
          label: '正在编辑',
          count: currentPlan ? 1 : 0,
          note: currentPlan ? `当前焦点为 ${currentPlan.name}，适合补设计、复制或重命名。` : '当前还没有载入编辑中的计划。',
        },
        {
          label: '待运行',
          count: pendingPlans,
          note: '这些计划还没有对应结果，适合优先形成 README / index / dashboard 闭环。',
        },
        {
          label: '已形成结果',
          count: withResults,
          note: '这些计划已经进入结果总览、对比、回放和 dashboard 预览工作流。',
        },
      ];
      const lineCoverageRows = workspaceLineCoverageRows(allRows).slice(0, 4);
      const manageFlowNodes = [
        {
          tone: 'accent',
          step: '1. 锁定焦点',
          title: focusPlan.name || '当前计划',
          detail: `${profile.line} · ${profile.status}`,
        },
        {
          tone: 'warm',
          step: '2. 补齐设计',
          title: lifecycle.label,
          detail: lifecycle.note,
        },
        {
          tone: focusDashboard ? 'ok' : 'warm',
          step: '3. 形成结果',
          title: focusDashboard ? `结果 ${focusDashboard.name}` : '等待首轮运行',
          detail: focusDashboard
            ? `通过率 ${percentText(focusDashboard.acceptance_rate)}，可继续做对比、回放和 dashboard 复查。`
            : '先校验并运行，形成 README / index / dashboard 这一套标准结果产物。',
        },
        {
          tone: 'ok',
          step: '4. 推进主线',
          title: profile.question,
          detail: profile.next,
        },
      ];
      const manageRouteSteps = [
        `先载入 ${focusPlan.name || '当前焦点计划'}，确认它属于 ${profile.line}。`,
        focusDashboard
          ? `结果 ${focusDashboard.name} 已存在，可直接回结果层继续读图或复核。`
          : '当前还没有结果，优先运行首轮实验，形成 README / index / dashboard 标准产物。',
        lifecycle.note,
      ];
      if (manageSideSignals) {
        manageSideSignals.innerHTML = `
          <div class="summary-card"><span>焦点计划</span><strong>${esc(focusPlan.name || '—')}</strong></div>
          <div class="summary-card"><span>结果状态</span><strong>${esc(focusDashboard ? '已有结果' : '待运行')}</strong></div>
          <div class="summary-card"><span>主线位置</span><strong>${esc(focusStage?.label || profile.line)}</strong></div>
          <div class="summary-card"><span>下一动作</span><strong>${esc(focusDashboard ? '回结果层' : '先校验并运行')}</strong></div>
        `;
      }
      if (manageSideRoute) {
        const routeCards = [
          {
            slot: '当前焦点',
            stage: focusStage,
            note: '当前选中的计划落在这条主线环节里，适合围绕它继续编辑或复跑。',
          },
          {
            slot: missingResultStage ? '优先补结果' : '优先补模板',
            stage: missingResultStage || missingPlanStage,
            note: missingResultStage
              ? '这条主线已经有计划但还缺代表结果，适合优先形成稳定 dashboard。'
              : '这条主线还没有稳定计划，适合先补一个代表模板或计划。',
          },
          {
            slot: '展示入口',
            stage: showcaseStage,
            note: '需要做演示或汇报时，优先从这一环进入更容易讲清主线价值。',
          },
        ].filter(item => item.stage);
        const uniqueRouteCards = [];
        const seenRouteIds = new Set();
        for (const item of routeCards) {
          if (seenRouteIds.has(item.stage.id)) continue;
          seenRouteIds.add(item.stage.id);
          uniqueRouteCards.push(item);
        }
        manageSideRoute.innerHTML = `
          <div class="history-detail" style="margin-bottom:0">
            <strong>主线建议</strong>
            <p>左侧固定给出焦点计划、当前缺口和展示入口，避免计划管理退回成单纯列表。</p>
            <div class="section-stack" style="margin-top:10px">
              ${uniqueRouteCards.map(item => `
                <div class="detail-box">
                  <strong>${esc(item.slot)} · ${esc(item.stage.step)} ${esc(item.stage.label)}</strong>
                  <div>${esc(item.stage.status)}。${esc(item.note)}</div>
                </div>
              `).join('')}
            </div>
          </div>
        `;
      }
      const cockpitView = ['focus', 'health', 'attention'].includes(state.manageCockpitView) ? state.manageCockpitView : 'focus';
      state.manageCockpitView = cockpitView;
      const focusMarkup = `
        <div class="history-detail" style="margin-bottom:10px">
          <strong>当前最短路径</strong>
          <p>计划管理首页先回答“现在该继续哪个计划、先编辑还是先看结果、下一步回哪条主线”。</p>
          <div class="ribbon">
            ${manageRouteSteps.map(step => `<span class="ribbon-step">${esc(step)}</span>`).join('')}
          </div>
          <div class="toolbar" style="margin-top:10px">
            <button type="button" onclick="showExperiment('${esc(focusPlan.path)}')">继续这个计划</button>
            <button class="secondary" type="button" onclick="${focusDashboard ? `showDashboard('${esc(focusDashboard.path)}')` : 'void(0)'}" ${focusDashboard ? '' : 'disabled'}>查看对应结果</button>
            <button class="secondary" type="button" onclick="switchManagePoolView('plans')">打开计划清单</button>
          </div>
        </div>
        <div class="detail-grid" style="margin-top:10px">
          <div class="detail-box"><strong>焦点计划定位</strong><div>${esc(focusPlan.name)} · ${esc(profile.line)} · ${esc(profile.status)}</div></div>
          <div class="detail-box"><strong>结果覆盖</strong><div>${focusDashboard ? `已生成结果 ${esc(focusDashboard.name)}，通过率 ${percentText(focusDashboard.acceptance_rate)}。` : '当前还没有对应结果，建议先校验后运行。'}</div></div>
          <div class="detail-box"><strong>当前问题</strong><div>${esc(profile.question)}</div></div>
          <div class="detail-box"><strong>推荐动作</strong><div>${esc(focusDashboard ? '继续编辑计划、复跑关键参数，或直接切到结果总览继续读图。' : '先完成首轮运行，形成 README / index / dashboard 这一套标准结果产物。')}</div></div>
        </div>
        <div class="toolbar" style="margin-top:10px">
          <button type="button" onclick="showExperiment('${esc(focusPlan.path)}')">载入焦点计划</button>
          <button class="secondary" type="button" onclick="${focusDashboard ? `showDashboard('${esc(focusDashboard.path)}')` : 'void(0)'}" ${focusDashboard ? '' : 'disabled'}>查看焦点结果</button>
          <button class="secondary" type="button" onclick="switchManageView('history')">检查版本与归档</button>
        </div>
        <details class="fold-panel" style="margin-top:10px">
          <summary>展开计划推进流程板<span>查看完整的计划生命周期与主线推进说明</span></summary>
          <div class="fold-panel-body">
            <p>把“锁定计划、补齐设计、形成结果、继续主线”放到同一层，计划管理先像研究工作台，再像文件池。</p>
            <div class="flow-sequence" style="margin-top:10px">
              ${manageFlowNodes.map(node => `
                <div class="flow-node ${esc(node.tone)}">
                  <small>${esc(node.step)}</small>
                  <strong>${esc(node.title)}</strong>
                  <p>${esc(node.detail)}</p>
                </div>
              `).join('')}
            </div>
          </div>
        </details>
      `;
      const healthMarkup = `
        <div class="history-detail" style="margin-top:10px; margin-bottom:0">
          <strong>实验生命周期看板</strong>
          <p>先按“模板、编辑、待运行、已有结果”来管理计划，再看研究主线覆盖，避免实验池只增长文件数量。</p>
          <div class="figure-guide-grid" style="margin-top:10px">
            ${lifecycleRows.map(row => `
              <div class="figure-guide-card">
                <span>计划阶段</span>
                <strong>${esc(row.label)} · ${esc(String(row.count))}</strong>
                <p>${esc(row.note)}</p>
              </div>
            `).join('')}
          </div>
          <div class="history-detail" style="margin:10px 0 0">
            <strong>研究主线覆盖</strong>
            <p>按实验主线检查当前工作区已经覆盖到哪里，避免计划缺少清晰实验谱系。</p>
            <div class="history-grid" style="margin-top:10px">
              ${lineCoverageRows.length ? lineCoverageRows.map(row => `
                <button class="history-card ${row.current ? 'active' : ''}" type="button" onclick="showExperiment('${esc(row.representative.path)}')">
                  <span>${esc(row.current ? '当前主线' : '实验主线')}</span>
                  <strong>${esc(row.line)}</strong>
                  <p>${esc(row.count)} 个计划 · ${esc(row.withResults)} 个已有结果<br>${esc(row.question)}</p>
                </button>
              `).join('') : '<div class="detail-box"><strong>主线覆盖</strong><div>当前还没有可总结的实验主线。</div></div>'}
            </div>
          </div>
        </div>
      `;
      const attentionMarkup = `
        <div class="history-detail" style="margin-top:10px; margin-bottom:0">
          <strong>待处理计划</strong>
          <p>这里专门收拢异常计划和待运行计划，减少在实验池里来回翻找。</p>
          <div class="history-grid" style="margin-top:10px">
            ${attentionRows.length ? attentionRows.map(plan => {
              const badge = plan.error ? '需修正' : '待运行';
              const text = plan.error
                ? plan.error
                : `场景 ${plan.scenario || '—'} · ${experimentScaleText(plan)}`;
              return `<button class="history-card" type="button" onclick="showExperiment('${esc(plan.path)}')"><span>${esc(badge)}</span><strong>${esc(plan.name)}</strong><p>${esc(text)}</p></button>`;
            }).join('') : '<div class="detail-box"><strong>当前状态</strong><div>实验池里暂时没有明显待处理计划，可以继续扩展新实验。</div></div>'}
          </div>
        </div>
      `;
      const cockpitMarkup = cockpitView === 'health'
        ? healthMarkup
        : cockpitView === 'attention'
          ? attentionMarkup
          : focusMarkup;
      managePoolCockpit.innerHTML = `
        <div class="history-detail" style="margin-bottom:0">
          <strong>计划资产总览</strong>
          <p>先判断当前计划池是否健康、哪些计划已有结果、哪一个最值得继续推进。</p>
          <div class="summary-grid" style="margin-top:10px">
            <div class="summary-card"><span>实验计划</span><strong>${allRows.length}</strong></div>
            <div class="summary-card"><span>筛选命中</span><strong>${filtered.length}</strong></div>
            <div class="summary-card"><span>已有结果</span><strong>${withResults}</strong></div>
            <div class="summary-card"><span>待运行</span><strong>${pendingPlans}</strong></div>
            <div class="summary-card"><span>异常计划</span><strong>${issuePlans}</strong></div>
            <div class="summary-card"><span>当前焦点</span><strong>${esc(focusPlan.name || '—')}</strong></div>
          </div>
          <div class="editor-view-shell" style="margin-top:12px">
            <div class="segment-control">
              <button class="${cockpitView === 'focus' ? 'active' : ''}" type="button" onclick="switchManageCockpitView('focus')">焦点计划</button>
              <button class="${cockpitView === 'health' ? 'active' : ''}" type="button" onclick="switchManageCockpitView('health')">资产健康</button>
              <button class="${cockpitView === 'attention' ? 'active' : ''}" type="button" onclick="switchManageCockpitView('attention')">待处理</button>
            </div>
            <div class="editor-view-note">${esc(manageCockpitSummaryText(cockpitView))}</div>
          </div>
          ${cockpitMarkup}
        </div>
      `;
    }

    function renderResultsOverviewCockpit() {
      if (!resultsOverviewCockpit) return;
      const dashboards = [...(state.workspace?.dashboards || [])].sort((a, b) => Number(b.updated_ts || 0) - Number(a.updated_ts || 0));
      const standardRouteMarkup = resultsStandardRouteMarkup(dashboards);
      const standardRouteStages = standardRouteStageRecords(dashboards);
      const branchRows = standardRouteStages.flatMap(stage => stage.branches || []);
      if (!dashboards.length) {
        if (resultsQuickstartPanel) {
          resultsQuickstartPanel.innerHTML = `
            <div class="history-detail" style="margin-bottom:0">
              <strong>结果入口板</strong>
              <p>当前工作区还没有结果。先从实验库载入标准计划，或者在创建器里生成并运行第一份实验。</p>
              <div class="summary-grid" style="margin-top:10px">
                <div class="summary-card"><span>当前结果</span><strong>0</strong></div>
                <div class="summary-card"><span>风险结果</span><strong>0</strong></div>
                <div class="summary-card"><span>主线分组</span><strong>0</strong></div>
                <div class="summary-card"><span>推荐动作</span><strong>先跑首轮实验</strong></div>
              </div>
              <div class="toolbar" style="margin-top:10px">
                <button type="button" onclick="switchLabView('library')">去实验库</button>
                <button class="secondary" type="button" onclick="switchLabView('builder')">去创建实验</button>
              </div>
            </div>
          `;
        }
        resultsOverviewCockpit.innerHTML = `
          <div class="callout">
            <strong>结果驾驶舱</strong>
            <p>当前还没有实验结果。建议先从“实验库”载入一个成熟实验，或在“创建实验”里生成计划并运行，平台会自动生成结果目录和 dashboard。</p>
          </div>
          ${standardRouteMarkup}
        `;
        return;
      }
      const focus = preferredDashboardRecord();
      const latest = dashboards[0];
      const needsAttention = dashboards.filter(item => Number(item.acceptance_rate || 0) < 0.999);
      const currentPlan = [...(state.workspace?.experiments || [])].find(plan => matchingDashboardForPlan(plan)?.path === focus?.path) || null;
      const focusBinding = focus ? standardAssetBindingRecord({result: focus, dashboards}) : null;
      const focusProtocol = focusBinding?.protocol || null;
      const focusNextProtocol = experimentProtocolConfig(focusProtocol?.nextProtocolId || '');
      const line = currentPlan ? planAssetProfile(currentPlan).line : '结果工作区';
      const focusSemanticMarkup = focus
        ? resultSemanticCardsHtml(focus, currentPlan, {
            dashboards,
            briefTitle: '焦点结果实验说明卡',
            briefLead: '结果总览首屏也直接说明这组实验在回答什么问题，而不是只给结果名称和通过率。',
            readingTitle: '焦点结果阅读任务书',
            readingLead: '先在总览层就把读图顺序、诊断入口和下一步动作讲清楚。',
            bindingTitle: '焦点结果主线资产链',
            showToolbar: false,
          })
        : '';
      const groupedLines = groupedDashboardLines(dashboards).slice(0, 4);
      const failureRank = [...needsAttention]
        .sort((a, b) => Number(a.acceptance_rate || 0) - Number(b.acceptance_rate || 0) || Number(b.failed_count || 0) - Number(a.failed_count || 0))
        .slice(0, 3);
      const figureCards = focus ? resultFigureCards(focus).slice(0, 3) : [];
      const routeSteps = focus ? cockpitReadingRoute(focus) : [];
      const representativeCards = groupedLines.map(([groupName]) => {
        const meta = lineDashboardAssetMeta(groupName, dashboards);
        const asset = meta.asset;
        const representative = meta.representative;
        const latestForLine = meta.latest;
        const active = representative?.path === focus?.path;
        return `
          <button class="history-card ${active ? 'active' : ''}" type="button" onclick="${representative ? `showDashboard('${esc(representative.path)}')` : latestForLine ? `showDashboard('${esc(latestForLine.path)}')` : 'void(0)'}" ${(representative || latestForLine) ? '' : 'disabled'}>
            <span>${esc(active ? '当前代表结果' : representative ? '代表结果' : latestForLine ? '仅有最近结果' : asset.status)}</span>
            <strong>${esc(groupName)}</strong>
            <p>${esc(asset.plan ? `计划 ${asset.plan.name}` : '计划待固化')} · ${esc(representative ? `代表 ${representative.name}` : '代表结果待稳定')}<br>${representative ? `${representativeDashboardSummary(representative)}` : esc(asset.next)}${latestForLine && !meta.latestIsRepresentative ? `<br>最近运行 ${esc(latestForLine.name)} · ${esc(latestForLine.updated_at || '刚更新')}` : ''}</p>
          </button>
        `;
      }).join('');
      const attentionCards = failureRank.map(item => {
        const lineName = resultLineForDashboard(item);
        return `
          <button class="history-card" type="button" onclick="showDashboard('${esc(item.path)}')">
            <span>${esc(dashboardRiskLabel(item))}</span>
            <strong>${esc(item.name)}</strong>
            <p>${esc(lineName)} · 通过率 ${percentText(item.acceptance_rate)} · 失败 ${fmt(item.failed_count)} 个 run<br>${esc(dashboardRiskReason(item))}</p>
          </button>
        `;
      }).join('');
      const lineMapCards = groupedLines.map(([groupName, count]) => {
        const meta = lineDashboardAssetMeta(groupName, dashboards);
        const representativeAsset = meta.asset;
        const representative = meta.representative;
        const latestForLine = meta.latest;
        return `
          <button class="history-card ${state.dashboardLineFilter === groupName ? 'active' : ''}" type="button" onclick="setDashboardLineFilter('${esc(groupName)}')">
            <span>${esc(groupName)}</span>
            <strong>${count} 个结果</strong>
            <p>${esc(representativeAsset.plan ? `代表计划 ${representativeAsset.plan.name}` : '代表计划待固化')} · ${esc(representative?.name || '暂无代表结果')}<br>${esc(representative ? representativeDashboardSummary(representative) : latestForLine ? `最近运行 ${latestForLine.name}` : '当前还没有稳定代表结果')}</p>
          </button>
        `;
      }).join('');
      const focusStatus = Number(focus?.acceptance_rate || 0) >= 0.999
        ? '当前焦点结果通过率正常，适合继续做图表阅读、对比和回放。'
        : '当前焦点结果存在失败 run，建议先看诊断页和最差 Run 解释，再决定是否回到计划管理修参数。';
      const actionCards = [
        {
          label: '动作 1',
          title: '先看焦点结果',
          detail: '进入“当前结果”的概要标签，先确认实验问题、通过率和最佳 run，再展开图表和诊断。',
          actionLabel: '打开焦点结果',
          actionCode: `showDashboard('${esc(focus?.path || latest.path)}')`,
        },
        {
          label: '动作 2',
          title: '再看关键对比',
          detail: '当这轮实验涉及参数、环境或任务差异时，优先去结果对比确认误差曲线和力矩代价。',
          actionLabel: '去结果对比',
          actionCode: "switchResultsView('compare')",
        },
        {
          label: '动作 3',
          title: '回放动态过程',
          detail: '如果你关心模式切换、边界工况或最差 run 的动态形态，就直接进入姿态回放和时间线。',
          actionLabel: '去姿态回放',
          actionCode: "switchResultsView('replay')",
        },
        {
          label: '动作 4',
          title: '回到计划主线',
          detail: currentPlan
            ? '如果当前结果已经暴露边界或失败原因，就回到对应计划继续收紧变量、任务或验收口径。'
            : '如果需要继续派生实验，就回到计划管理或实验库，让结果回到可复现实验主线里。',
          actionLabel: currentPlan ? '回到对应计划' : '去计划管理',
          actionCode: currentPlan ? `showExperiment('${esc(currentPlan.path)}')` : "switchLabView('manage')",
        },
      ];
      if (resultsQuickstartPanel) {
        resultsQuickstartPanel.innerHTML = `
          <div class="history-detail" style="margin-bottom:0">
            <strong>结果入口板</strong>
            <p>右侧固定保留焦点结果、风险出口和回计划入口，让结果页首屏更像控制台而不是结果文档。</p>
            <div class="summary-grid" style="margin-top:10px">
              <div class="summary-card"><span>焦点结果</span><strong>${esc(focus?.name || '—')}</strong></div>
              <div class="summary-card"><span>风险结果</span><strong>${needsAttention.length}</strong></div>
              <div class="summary-card"><span>主线分组</span><strong>${groupedLines.length}</strong></div>
              <div class="summary-card"><span>推荐动作</span><strong>${esc(needsAttention.length ? '先看风险' : '先读焦点')}</strong></div>
            </div>
            <div class="detail-grid" style="margin-top:10px">
              <div class="detail-box">
                <strong>当前焦点</strong>
                <div>${esc(focus?.scenario || '—')} · 最佳 run ${esc(focus?.best_run_id || '—')} · ${fmt(focus?.best_final_error_deg)} deg</div>
              </div>
              <div class="detail-box">
                <strong>最新运行</strong>
                <div>${esc(latest?.name || '—')} · 通过率 ${percentText(latest?.acceptance_rate || 0)} · 失败 ${fmt(latest?.failed_count || 0)} 个 run</div>
              </div>
              <div class="detail-box">
                <strong>推荐图表入口</strong>
                <div>${esc(figureCards.length ? `${figureCards[0].label} ${figureCards[0].title}` : '先载入焦点结果后再生成图表入口')}</div>
              </div>
              <div class="detail-box">
                <strong>下一跳</strong>
                <div>${esc(currentPlan ? `必要时回到计划 ${currentPlan.name} 收紧变量、任务和验收。` : '必要时回到计划管理，把结果重新接回标准实验主线。')}</div>
              </div>
            </div>
            <div class="toolbar" style="margin-top:10px">
              <button type="button" onclick="switchResultsOverviewView('focus'); showDashboard('${esc(focus?.path || latest.path)}')">打开焦点结果</button>
              <button class="secondary" type="button" onclick="${needsAttention.length ? `switchResultsCockpitView('risk')` : `switchResultsView('compare')`}">${needsAttention.length ? '打开风险清单' : '去结果对比'}</button>
              <button class="secondary" type="button" onclick="switchResultsView('replay')">去姿态回放</button>
              <button class="secondary" type="button" onclick="${currentPlan ? `showExperiment('${esc(currentPlan.path)}')` : `switchLabView('manage')`}">${currentPlan ? '回到对应计划' : '去计划管理'}</button>
            </div>
            ${focusSemanticMarkup}
          </div>
        `;
      }
      const lineSummaryBoxes = groupedLines.length
        ? groupedLines.map(([groupName, count], index) => {
            const meta = lineDashboardAssetMeta(groupName, dashboards);
            const representative = meta.representative;
            const latestForLine = meta.latest;
            const best = meta.best;
            return `
              <div class="detail-box">
                <strong>${esc(index === 0 ? '主线焦点' : `主线 ${index + 1}`)}</strong>
                <div>${esc(groupName)} · ${count} 个结果<br>${esc(representative ? `代表 ${representative.name}` : latestForLine ? `最近 ${latestForLine.name}` : '暂无代表结果')} · ${esc(representative ? representativeDashboardSummary(representative) : best ? `当前最佳误差 ${fmt(best.best_final_error_deg)} deg` : '建议先补稳定代表结果')}</div>
              </div>
            `;
          }).join('')
        : '<div class="detail-box"><strong>主线结果</strong><div>当前还没有足够结果形成主线分组。</div></div>';
      const routeRibbon = routeSteps.length
        ? routeSteps.map(step => `<span class="ribbon-step">${esc(step)}</span>`).join('')
        : '<span class="ribbon-step">先载入一个焦点结果，再从摘要、图表和链路逐步阅读。</span>';
      const quickReadingSteps = [
        `先打开 ${focus?.name || latest.name || '当前焦点结果'}，确认摘要和通过率。`,
        needsAttention.length
          ? `当前有 ${needsAttention.length} 个待关注结果，优先查看风险列表。`
          : '当前风险较低，可以直接去关键图表、对比或回放。',
        currentPlan
          ? `如果结论不稳，回到计划 ${currentPlan.name} 继续收紧变量、任务和验收。`
          : '如果需要继续派生实验，回到计划管理或实验库整理新的标准计划。',
      ];
      const cockpitFlowNodes = [
        {
          tone: 'accent',
          step: '焦点',
          title: focus?.name || '当前焦点结果',
          body: `${focus?.scenario || '—'} · run ${fmt(focus?.run_count)} · 通过率 ${percentText(focus?.acceptance_rate)}`,
        },
        {
          tone: needsAttention.length ? 'warm' : 'ok',
          step: '风险',
          title: needsAttention.length ? `${needsAttention.length} 个待关注结果` : '结果整体稳定',
          body: needsAttention.length
            ? '先看失败结果排行和最差 run，再决定是否回到计划管理收紧变量。'
            : '可以优先进入图表、对比和回放，沉淀代表结果。',
        },
        {
          tone: 'accent',
          step: '图表',
          title: figureCards.length ? `${figureCards[0].label} ${figureCards[0].title}` : '等待推荐图表',
          body: figureCards.length
            ? figureCards[0].purpose
            : '载入焦点结果后，这里会给出最值得先看的图表入口。',
        },
        {
          tone: 'ok',
          step: '下一步',
          title: currentPlan ? '回到计划或继续读图' : '继续结果阅读',
          body: needsAttention.length
            ? '建议先诊断失败结果，再回到计划或重新运行。'
            : '建议先读摘要，再去结果对比或姿态回放。',
        },
      ];
      const cockpitView = ['focus', 'risk', 'route'].includes(state.resultsCockpitView) ? state.resultsCockpitView : 'focus';
      state.resultsCockpitView = cockpitView;
      const focusMarkup = `
        <div class="history-detail" style="margin-bottom:10px">
          <strong>当前最短阅读路径</strong>
          <p>结果首页先把“先看谁、再看哪里、最后回到哪里”压成一条短路径，避免掉进长列表。</p>
          <div class="ribbon">
            ${quickReadingSteps.map(step => `<span class="ribbon-step">${esc(step)}</span>`).join('')}
          </div>
          <div class="toolbar" style="margin-top:10px">
            <button type="button" onclick="showDashboard('${esc(focus?.path || latest.path)}')">打开焦点结果</button>
            <button class="secondary" type="button" onclick="${needsAttention.length ? `switchResultsCockpitView('risk')` : `switchResultsView('compare')`}">${needsAttention.length ? '先看风险结果' : '去结果对比'}</button>
            <button class="secondary" type="button" onclick="${currentPlan ? `showExperiment('${esc(currentPlan.path)}')` : `switchLabView('manage')`}">${currentPlan ? '回到对应计划' : '去计划管理'}</button>
          </div>
        </div>
        <details class="fold-panel" open>
          <summary>结果分析流程板<span>默认展开，只保留首页判断路径</span></summary>
          <div class="fold-panel-body">
            <p>先判断焦点结果、风险状态、推荐图表和下一步动作，再进入细节页。这样结果阅读更像 academic cockpit，而不是直接掉进长列表。</p>
            <div class="flow-sequence" style="margin-top:10px">
              ${cockpitFlowNodes.map(node => `
                <div class="flow-node ${esc(node.tone)}">
                  <small>${esc(node.step)}</small>
                  <strong>${esc(node.title)}</strong>
                  <p>${esc(node.body)}</p>
                </div>
              `).join('')}
            </div>
          </div>
        </details>
        <div class="grid" style="margin-top:10px">
          <div class="history-detail" style="margin-bottom:0">
            <strong>焦点结果画布</strong>
            <p>把当前最值得看的结果放成一张画布，先说明它是谁、为什么值得看、下一跳该去哪里。</p>
            <div class="detail-grid" style="margin-top:10px">
              <div class="detail-box"><strong>焦点结果</strong><div>${esc(focus?.name || '—')} · ${esc(focus?.scenario || '—')}</div></div>
              <div class="detail-box"><strong>协议阶段</strong><div>${esc(focusProtocol?.label || '未映射标准协议')}</div></div>
              <div class="detail-box"><strong>最佳 Run</strong><div>${esc(focus?.best_run_id || '—')} · ${fmt(focus?.best_final_error_deg)} deg</div></div>
              <div class="detail-box"><strong>风险判断</strong><div>${esc(dashboardRiskLabel(focus))} · ${esc(dashboardRiskReason(focus))}</div></div>
              <div class="detail-box"><strong>对应计划</strong><div>${esc(currentPlan?.name || '当前未匹配到计划文件')}</div></div>
              <div class="detail-box"><strong>下一协议</strong><div>${esc(focusNextProtocol ? `${focusNextProtocol.label}：${focusNextProtocol.summary}` : '当前协议已收束，适合整理代表结果或回到计划管理派生下一版。')}</div></div>
            </div>
            <div class="toolbar" style="margin-top:10px">
              <button type="button" onclick="showDashboard('${esc(focus?.path || latest.path)}')">打开焦点结果</button>
              <button class="secondary" type="button" onclick="switchResultsView('compare')">去结果对比</button>
              <button class="secondary" type="button" onclick="switchResultsView('replay')">去姿态回放</button>
            </div>
            ${focusBinding ? `
              <details class="fold-panel" style="margin-top:10px">
                <summary>展开当前代表资产<span>查看标准模板、代表计划、代表结果和下一步实验</span></summary>
                <div class="fold-panel-body">
                  ${standardAssetBindingHtml(focusBinding, {title: '当前代表资产', showToolbar: true})}
                </div>
              </details>
            ` : ''}
          </div>
          <div class="history-detail" style="margin-bottom:0">
            <strong>结果首页行动板</strong>
            <p>首页只保留四个最值得做的动作，让结果阅读从“知道该看什么”直接过渡到“知道下一步点哪里”。</p>
            <div class="figure-guide-grid" style="margin-top:10px">
              ${actionCards.map(card => `
                <div class="figure-guide-card">
                  <span>${esc(card.label)}</span>
                  <strong>${esc(card.title)}</strong>
                  <p>${esc(card.detail)}</p>
                  <div class="toolbar" style="margin-top:10px">
                    <button type="button" onclick="${card.actionCode}">${esc(card.actionLabel)}</button>
                  </div>
                </div>
              `).join('')}
            </div>
          </div>
        </div>
      `;
      const riskMarkup = `
        <div class="summary-grid" style="margin-top:10px">
          <div class="summary-card"><span>待关注结果</span><strong>${needsAttention.length}</strong></div>
          <div class="summary-card"><span>失败排行</span><strong>${failureRank.length || 0}</strong></div>
          <div class="summary-card"><span>当前判断</span><strong>${needsAttention.length ? '先诊断' : '整体稳定'}</strong></div>
          <div class="summary-card"><span>主线分组</span><strong>${groupedLines.length}</strong></div>
        </div>
        <div class="history-detail" style="margin:10px 0 0">
          <strong>风险结果清单</strong>
          <p>这里只保留最需要优先处理的结果。先开失败最多或通过率最低的实验，再决定是否回到计划管理收紧变量、任务或验收口径。</p>
          <div class="history-grid" style="margin-top:10px">
            ${attentionCards || '<div class="detail-box"><strong>当前状态</strong><div>当前结果库里暂时没有高风险结果，整体状态比较稳定。</div></div>'}
          </div>
        </div>
        <div class="detail-grid" style="margin-top:10px">
          <div class="detail-box"><strong>失败结果排行</strong><div>${failureRank.length ? failureRank.map((item, index) => `${index + 1}. ${item.name} · 通过率 ${percentText(item.acceptance_rate)} · 失败 ${fmt(item.failed_count)} 个 run`).join('<br>') : '当前结果库里没有失败结果，整体状态比较稳定。'}</div></div>
          <div class="detail-box"><strong>风险判断</strong><div>${esc(focusStatus)}</div></div>
          <div class="detail-box"><strong>当前优先动作</strong><div>${esc(needsAttention.length ? '先开风险结果看诊断和最差 run，再决定是否回到计划管理。' : '当前没有明显风险结果，可以直接进入焦点结果或图表阅读。')}</div></div>
          <div class="detail-box"><strong>风险出口</strong><div>${esc(needsAttention.length ? '建议从这里直接跳到当前结果的诊断页或结果对比。' : '风险较低时，可直接切回焦点结果或主线导航继续读图。')}</div></div>
        </div>
      `;
      const routeMarkup = `
        <div class="history-detail" style="margin-bottom:0">
          <strong>代表结果看板</strong>
          <p>先看每条实验主线当前最适合代表它的结果，再决定要继续读哪一环，而不是直接进入完整结果列表。</p>
          <div class="history-grid" style="margin-top:10px">
            ${representativeCards || '<div class="detail-box"><strong>当前状态</strong><div>当前还没有可作为主线代表资产的结果，建议先补首轮标准结果。</div></div>'}
          </div>
        </div>
        <div class="history-detail" style="margin:10px 0 0">
          <strong>主线结果导航</strong>
          <p>把标准实验路径、结果分组和首页阅读路线放在同一层，结果页就能真正接回实验主线。</p>
          <div class="detail-grid" style="margin-top:10px">
            ${lineSummaryBoxes}
          </div>
          <div class="history-grid" style="margin-top:10px">
            <button class="history-card ${state.dashboardLineFilter === 'all' ? 'active' : ''}" type="button" onclick="setDashboardLineFilter('all')">
              <span>全部主线</span>
              <strong>${dashboards.length} 个结果</strong>
              <p>清空主线筛选，回到完整结果库。</p>
            </button>
            ${lineMapCards}
          </div>
        </div>
        <div class="history-detail" style="margin:10px 0 0">
          <strong>首页阅读路线</strong>
          <p>结果首页先把“先看什么、再看什么、最后回到哪里”讲清楚，减少在结果列表、图表和计划页之间反复试探。</p>
          <div class="ribbon">
            ${routeRibbon}
          </div>
            <div class="detail-grid" style="margin-top:10px">
              ${routeSteps.map((step, index) => `<div class="detail-box"><strong>第 ${index + 1} 步</strong><div>${esc(step)}</div></div>`).join('')}
              <div class="detail-box"><strong>关键支线</strong><div>${branchRows.length ? branchRows.map(branch => `${branch.label}（锚点 ${experimentProtocolConfig(branch.anchorProtocolId)?.label || branch.anchorProtocolId}）`).join('；') : '当前主线没有单独抽出的关键支线。'}</div></div>
            </div>
          </div>
        <details class="fold-panel" style="margin-top:10px">
          <summary>展开标准实验主线看板<span>按需查看完整主线覆盖、代表结果和下一环入口</span></summary>
          <div class="fold-panel-body">
            ${standardRouteMarkup}
          </div>
        </details>
        <details class="fold-panel" style="margin-top:10px">
          <summary>展开图表与主线导航<span>按需查看推荐图表入口和完整主线分组</span></summary>
          <div class="fold-panel-body">
            <p>这里把焦点结果的推荐图表入口和实验主线放在一起，方便从首页直接进入更细的读图步骤。</p>
            <div class="figure-guide-grid" style="margin-top:10px">
              ${figureCards.length ? figureCards.map(card => `
                <div class="figure-guide-card">
                  <span>${esc(card.label)}</span>
                  <strong>${esc(card.title)}</strong>
                  <p>${esc(card.purpose)}</p>
                  <div class="toolbar" style="margin-top:10px">
                    <button type="button" onclick="${card.actionCode || 'void(0)'}" ${card.actionCode ? '' : 'disabled'}>${esc(card.actionLabel || '查看')}</button>
                  </div>
                </div>
              `).join('') : '<div class="detail-box"><strong>推荐图表入口</strong><div>当前没有可推荐的图表卡片。</div></div>'}
            </div>
            <div class="detail-grid" style="margin-top:10px">
              <div class="detail-box"><strong>实验主线分组</strong><div>${groupedLines.length ? groupedLines.map(([name, count]) => `${name} × ${count}`).join('；') : '当前还没有可分组结果。'}</div></div>
              <div class="detail-box"><strong>推荐图表入口</strong><div>${figureCards.length ? figureCards.map(card => `${card.label} ${card.title}`).join('；') : '先载入一个结果后，这里会显示推荐图表入口。'}</div></div>
              <div class="detail-box"><strong>当前阅读节奏</strong><div>${esc(focusStatus)}</div></div>
            </div>
          </div>
        </details>
      `;
      const cockpitMarkup = cockpitView === 'risk'
        ? riskMarkup
        : cockpitView === 'route'
          ? routeMarkup
          : focusMarkup;
      resultsOverviewCockpit.innerHTML = `
        <div class="history-detail" style="margin-bottom:0">
          <strong>结果驾驶舱</strong>
          <p>先判断现在该看哪个结果、有没有失败项，以及下一步是读图还是回计划。</p>
          <div class="summary-grid" style="margin-top:10px">
            <div class="summary-card"><span>结果目录</span><strong>${dashboards.length}</strong></div>
            <div class="summary-card"><span>最新运行</span><strong>${esc(latest.name || '—')}</strong></div>
            <div class="summary-card"><span>当前焦点</span><strong>${esc(focus?.name || '—')}</strong></div>
            <div class="summary-card"><span>待关注结果</span><strong>${needsAttention.length}</strong></div>
            <div class="summary-card"><span>最佳 Run</span><strong>${esc(focus?.best_run_id || '—')}</strong></div>
            <div class="summary-card"><span>焦点通过率</span><strong>${percentText(focus?.acceptance_rate)}</strong></div>
          </div>
          <div class="detail-grid" style="margin-top:10px">
            <div class="detail-box"><strong>焦点结果说明</strong><div>${esc(focus?.name || '—')} · 场景 ${esc(focus?.scenario || '—')} · run ${fmt(focus?.run_count)}</div></div>
            <div class="detail-box"><strong>实验主线</strong><div>${esc(line)}</div></div>
            <div class="detail-box"><strong>当前判断</strong><div>${esc(focusStatus)}</div></div>
            <div class="detail-box"><strong>推荐下一步</strong><div>${esc(needsAttention.length ? '优先打开存在失败的结果，查看诊断与链路，再回到计划管理收紧变量和验收门限。' : '先读当前焦点结果的概要和图表，再进入关键 Run 对比或姿态回放。')}</div></div>
          </div>
          <div class="editor-view-shell" style="margin-top:12px">
            <div class="segment-control">
              <button class="${cockpitView === 'focus' ? 'active' : ''}" type="button" onclick="switchResultsCockpitView('focus')">焦点结果</button>
              <button class="${cockpitView === 'risk' ? 'active' : ''}" type="button" onclick="switchResultsCockpitView('risk')">风险结果</button>
              <button class="${cockpitView === 'route' ? 'active' : ''}" type="button" onclick="switchResultsCockpitView('route')">主线导航</button>
            </div>
            <div class="editor-view-note">${esc(resultsCockpitSummaryText(cockpitView))}</div>
          </div>
          ${cockpitMarkup}
        </div>
      `;
    }

    function renderEditorPlanContext(plan = null) {
      if (!editorPlanContext) return;
      if (!plan || !state.currentExperimentMapping) {
        editorPlanContext.innerHTML = '载入实验计划后，这里会显示当前计划在实验平台中的定位、研究问题和推荐下一步。';
        return;
      }
      const mapping = state.currentExperimentMapping || {};
      const metadata = mapping.metadata || {};
      const mission = mapping.mission || {};
      const runtime = mapping.runtime || {};
      const acceptance = mapping.acceptance || {};
      const research = planResearchMeta(plan);
      const profile = research.profile;
      const dashboard = matchingDashboardForPlan(plan);
      const tags = Array.isArray(metadata.tags) ? metadata.tags : [];
      const sweepRows = planSweepSummaryRows(mapping);
      const acceptanceRows = acceptanceProfileRows(mapping);
      const lifecycle = planLifecycleStage(plan);
      const missionLabel = mission.template === 'detumble_then_hold'
        ? `消旋后保持 / ${mission.hold_mode || 'inertial_hold'}`
        : `单模式保持 / ${mission.mode || 'inertial_hold'}`;
      const runtimeLabel = runtime.template || runtime.name || 'single_rate';
      const acceptanceChips = acceptanceRows.map(item => `${item.label} ${item.value}`);
      const readiness = planReadinessText(mapping, dashboard);
      const roadmap = research.roadmap;
      const activeRoadmapId = research.currentId || roadmap.ids[0] || '';
      const activeRoadmapIndex = roadmap.ids.indexOf(activeRoadmapId);
      const nextRoadmapId = activeRoadmapIndex >= 0 ? (roadmap.ids[activeRoadmapIndex + 1] || '') : '';
      const nextRoadmapConfig = curatedExperimentConfig(nextRoadmapId);
      const briefMeta = experimentBriefMeta({
        experimentId: research.currentId,
        config: research.config || profile.config || {planName: metadata.name || plan.name, theme: profile.line},
        detailMeta: research.detail,
        academicMeta: research.academic,
        standardMeta: research.standard,
        question: metadata.description || profile.question || research.config?.question,
        scenarioPath: resolveCuratedExperimentScenario(research.config) || plan.scenario || mapping.scenario || '',
        sweepRows,
        sweepPath: sweepRows[0]?.path || '',
        secondSweepPath: sweepRows[1]?.path || '',
        variableLabel: sweepRows.length ? sweepRows.map(row => row.label).join(' / ') : (research.config?.variable || ''),
        mission: mission.template || 'single_mode',
        mode: mission.template === 'detumble_then_hold'
          ? (mission.hold_mode || mission.mode || 'inertial_hold')
          : (mission.mode || 'inertial_hold'),
        reference: mission.reference || mission.target || 'body_zero',
        detumbleS: mission.detumble_s || mission.detumble_duration_s || 0,
        acceptanceRows,
        mcSamples: Number(mapping?.monte_carlo?.samples || 0),
        nextSummary: nextRoadmapConfig
          ? (experimentLibraryDetailMeta(nextRoadmapId, nextRoadmapConfig).baseline || nextRoadmapConfig.description || research.detail.next)
          : profile.next,
        figuresSummary: research.standard.figurePackage || research.academic.plots,
      });
      const contextView = ['profile', 'brief', 'roadmap'].includes(state.editorContextView) ? state.editorContextView : 'profile';
      state.editorContextView = contextView;
      const planFlowNodes = [
        {
          tone: 'accent',
          step: '1. 研究问题',
          title: profile.question,
          detail: metadata.description || research.detail.baseline || '当前计划用于组织一组可复现实验。',
        },
        {
          tone: sweepRows.length ? 'warm' : '',
          step: '2. 变量设计',
          title: sweepRows.length ? `${sweepRows.length} 个扫描维度` : '单场景 / 无扫描',
          detail: sweepRows.length
            ? sweepRows.map(row => `${row.label} ${row.count} 档`).join('；')
            : '当前计划没有 sweep 或 Monte Carlo，更适合先形成基线结果。',
        },
        {
          tone: acceptanceRows.length ? 'ok' : 'warm',
          step: '3. 验收口径',
          title: acceptanceRows.length ? '已有通过标准' : '待补验收标准',
          detail: acceptanceRows.length
            ? acceptanceRows.map(row => `${row.label} ${row.value}`).join('；')
            : '建议补充末端误差、RMS 误差或峰值力矩阈值，避免结果难以判读。',
        },
        {
          tone: dashboard ? 'ok' : 'warm',
          step: '4. 结果联动',
          title: dashboard ? `已联动 ${dashboard.name}` : '尚未联动结果目录',
          detail: dashboard
            ? `最佳 run ${dashboard.best_run_id || '—'}，最佳末端误差 ${fmt(dashboard.best_final_error_deg)} deg。`
            : '运行后会自动接入结果总览、关键对比、姿态回放和 dashboard 预览。',
        },
        {
          tone: 'ok',
          step: '5. 主线推进',
          title: nextRoadmapConfig?.label || '继续当前实验主线',
          detail: nextRoadmapConfig
            ? (experimentLibraryDetailMeta(nextRoadmapId, nextRoadmapConfig).baseline || nextRoadmapConfig.description || research.detail.next)
            : profile.next,
        },
      ];
      const roadmapSteps = roadmap.ids.map((id, index) => {
        const config = curatedExperimentConfig(id);
        const detail = experimentLibraryDetailMeta(id, config);
        const isCurrent = index === activeRoadmapIndex;
        const isUpcoming = index > activeRoadmapIndex;
        const statusLabel = research.currentId
          ? (index < activeRoadmapIndex ? '前置实验' : isCurrent ? '当前所处环节' : '推荐下一步')
          : (isCurrent ? '推荐起点' : '后续阶段');
        return `
          <div class="roadmap-step ${isCurrent ? 'current' : isUpcoming ? 'upcoming' : ''}">
            <span>${esc(statusLabel)} · 第 ${index + 1} 步</span>
            <strong>${esc(config?.label || id)}</strong>
            <p>${esc(config?.question || config?.description || '当前实验用于形成可复现实验资产。')}</p>
            <p>${esc(detail.platform)}</p>
          </div>
        `;
      }).join('');
      const profileMarkup = `
        <div class="detail-grid" style="margin-top:10px">
          <div class="detail-box"><strong>当前研究问题</strong><div>${esc(profile.question)}</div></div>
          <div class="detail-box"><strong>结果状态</strong><div>${dashboard ? `已匹配结果 ${esc(dashboard.name)}，通过率 ${Math.round(Number(dashboard.acceptance_rate || 0) * 1000) / 10}%。` : '当前还没有结果，适合先校验再运行。'}</div></div>
          <div class="detail-box"><strong>标签</strong><div class="chips">${tags.length ? tags.map(tag => `<span class="chip">${esc(tag)}</span>`).join('') : '<span class="subtle">当前计划没有 metadata.tags。</span>'}</div></div>
          <div class="detail-box"><strong>验收规则</strong><div class="chips">${acceptanceChips.length ? acceptanceChips.map(text => `<span class="chip">${esc(text)}</span>`).join('') : '<span class="subtle">当前计划没有显式验收规则。</span>'}</div></div>
        </div>
        <div class="detail-grid" style="margin-top:10px">
          <div class="detail-box"><strong>变量矩阵</strong><div>${sweepRows.length ? sweepRows.map(row => `${row.label}（${row.path}） · ${row.count} 档`).join('；') : '当前计划没有启用扫描变量或 Monte Carlo。'}</div></div>
          <div class="detail-box"><strong>验收口径</strong><div>${acceptanceRows.length ? acceptanceRows.map(row => `${row.label} ${row.value}`).join('；') : '当前计划还没有明确通过标准。'}</div></div>
          <div class="detail-box"><strong>结果联动</strong><div>${dashboard ? `已联动 ${esc(dashboard.name)}，最佳 run ${esc(dashboard.best_run_id || '—')}，最佳误差 ${fmt(dashboard.best_final_error_deg)} deg。` : '当前还没有对应结果目录，运行后会自动接入结果总览、对比和回放。'}</div></div>
          <div class="detail-box"><strong>计划成熟度</strong><div>${esc(readiness)}</div></div>
        </div>
        <div class="history-detail" style="margin:10px 0 0">
          <strong>当前计划流程图</strong>
          <p>用一条可读流程把“研究问题、变量、验收、结果、主线推进”串起来，减少你在编辑器、结果页和实验库之间来回跳转。</p>
          <div class="flow-sequence" style="margin-top:10px">
            ${planFlowNodes.map(node => `
              <div class="flow-node ${esc(node.tone)}">
                <small>${esc(node.step)}</small>
                <strong>${esc(node.title)}</strong>
                <p>${esc(node.detail)}</p>
              </div>
            `).join('')}
          </div>
        </div>
        <div class="history-detail" style="margin:10px 0 0">
          <strong>变量与验收摘要</strong>
          <p>把扫描设计、验收标准和结果联动放在同一层，减少在 JSON、结果页和实验库之间来回跳转。</p>
          <div class="detail-grid" style="margin-top:10px">
            ${sweepRows.length ? sweepRows.map(row => `<div class="detail-box"><strong>${esc(row.name)}</strong><div>${esc(row.label)} · ${esc(row.values.slice(0, 4).join('、') || '—')}${row.values.length > 4 ? ' …' : ''}</div></div>`).join('') : '<div class="detail-box"><strong>扫描设计</strong><div>当前没有启用 sweep 或 Monte Carlo。</div></div>'}
            <div class="detail-box"><strong>验收摘要</strong><div>${acceptanceRows.length ? acceptanceRows.map(row => `${row.label} ${row.value}`).join('；') : '当前没有显式阈值。'}</div></div>
          </div>
        </div>
      `;
      const briefMarkup = `
        ${experimentBriefHtml(briefMeta, {
          title: '实验说明卡',
          lead: '当前计划也按实验库的 7 个说明项来阅读，避免“看 JSON”和“看实验”是两套语言。',
          compact: true,
        })}
        <div class="history-detail" style="margin:10px 0 0">
          <strong>当前阶段与建议</strong>
          <p>把计划生命周期、学术假设和建议图表压成一个工作区，便于快速判断这份计划现在该补什么。</p>
          <div class="detail-grid" style="margin-top:10px">
            <div class="detail-box"><strong>当前阶段</strong><div>${esc(lifecycle.label)} · ${esc(lifecycle.note)}</div></div>
            <div class="detail-box"><strong>实验假设</strong><div>${esc(research.academic.hypothesis)}</div></div>
            <div class="detail-box"><strong>适用前提</strong><div>${esc(research.academic.prerequisites)}</div></div>
            <div class="detail-box"><strong>建议图表</strong><div>${esc(research.academic.plots)}</div></div>
            <div class="detail-box"><strong>结论线索</strong><div>${esc(research.academic.takeaway)}</div></div>
            <div class="detail-box"><strong>计划动作建议</strong><div>${esc(dashboard ? '当前计划已形成结果，适合继续读图、复制副本并沿主线派生下一轮实验。' : '当前计划还适合先补验收与变量细节，再运行形成第一版标准结果目录。')}</div></div>
          </div>
        </div>
      `;
      const roadmapMarkup = `
        <div class="history-detail" style="margin:10px 0 0">
          <strong>计划标准卡</strong>
          <p>把这个计划放回实验协议、结果图包和派生节奏里理解，计划管理就不只是“编辑 JSON”，而更像成熟平台里的 experiment workspace。</p>
          <div class="detail-grid" style="margin-top:10px">
            <div class="detail-box"><strong>推荐验收</strong><div>${esc(research.standard.acceptance)}</div></div>
            <div class="detail-box"><strong>重点图表包</strong><div>${esc(research.standard.figurePackage)}</div></div>
            <div class="detail-box"><strong>推荐派生版本</strong><div>${esc(research.standard.derivedPlan)}</div></div>
            <div class="detail-box"><strong>协议阶段</strong><div>${esc(research.standard.maturity)}</div></div>
            <div class="detail-box"><strong>资产定位</strong><div>${esc(research.standard.assetRole)}</div></div>
            <div class="detail-box"><strong>当前环节价值</strong><div>${esc(research.detail.platform)}</div></div>
          </div>
        </div>
        <div class="history-detail" style="margin:10px 0 0">
          <strong>实验主线导航</strong>
          <p>${esc(roadmap.description)} ${esc(research.currentId ? '当前计划已匹配到标准实验主线，可以直接沿这条链路继续推进。' : '当前计划暂未直接匹配标准实验，平台已按实验主线给出最接近的路线建议。')}</p>
          <div class="roadmap-stack">${roadmapSteps}</div>
          <div class="detail-grid" style="margin-top:10px">
            <div class="detail-box"><strong>当前所处环节</strong><div>${esc(research.detail.platform)}</div></div>
            <div class="detail-box"><strong>推荐继续实验</strong><div>${esc(nextRoadmapConfig ? (experimentLibraryDetailMeta(nextRoadmapId, nextRoadmapConfig).baseline || nextRoadmapConfig.description || '继续沿实验主线推进。') : research.detail.next)}</div></div>
          </div>
          <div class="toolbar" style="margin-top:10px">
            <button type="button" onclick="${activeRoadmapId ? `previewRoadmapExperiment('${esc(activeRoadmapId)}')` : 'void(0)'}" ${activeRoadmapId ? '' : 'disabled'}>在实验库中定位</button>
            <button class="secondary" type="button" onclick="${activeRoadmapId ? `openRoadmapExperiment('${esc(activeRoadmapId)}')` : 'void(0)'}" ${activeRoadmapId ? '' : 'disabled'}>打开当前主线实验</button>
            <button class="secondary" type="button" onclick="${nextRoadmapId ? `previewRoadmapExperiment('${esc(nextRoadmapId)}')` : 'void(0)'}" ${nextRoadmapId ? '' : 'disabled'}>继续到下一组实验</button>
          </div>
        </div>
      `;
      const contextMarkup = contextView === 'brief'
        ? briefMarkup
        : contextView === 'roadmap'
          ? roadmapMarkup
          : profileMarkup;
      editorPlanContext.innerHTML = `
        <strong>当前计划画像</strong>
        <p style="margin:8px 0 0;color:var(--muted);line-height:1.6">${esc(metadata.description || profile.question || '当前计划用于组织一组可复现实验。')}</p>
        <div class="summary-grid" style="margin-top:10px">
          <div class="summary-card"><span>实验主线</span><strong>${esc(profile.line)}</strong></div>
          <div class="summary-card"><span>资产状态</span><strong>${esc(profile.status)}</strong></div>
          <div class="summary-card"><span>任务流程</span><strong>${esc(missionLabel)}</strong></div>
          <div class="summary-card"><span>运行时模板</span><strong>${esc(runtimeLabel)}</strong></div>
        </div>
        <div class="editor-view-shell" style="margin-top:12px">
          <div class="segment-control">
            <button class="${contextView === 'profile' ? 'active' : ''}" type="button" onclick="setEditorContextView('profile')">计划画像</button>
            <button class="${contextView === 'brief' ? 'active' : ''}" type="button" onclick="setEditorContextView('brief')">实验说明</button>
            <button class="${contextView === 'roadmap' ? 'active' : ''}" type="button" onclick="setEditorContextView('roadmap')">主线导航</button>
          </div>
          <div class="editor-view-note">${esc(editorContextViewNote(contextView))}</div>
        </div>
        ${contextMarkup}
        <div class="callout info" style="margin:10px 0 0">
          <strong>推荐下一步</strong>
          <p>${esc(profile.next)}</p>
        </div>
        <div class="toolbar" style="margin-top:10px">
          <button type="button" onclick="switchManageView('pool')">返回实验池</button>
          <button class="secondary" type="button" onclick="${profile.curated ? `focusExperimentLibrary('${esc(profile.curatedId)}')` : `switchLabView('library')`}">${profile.curated ? '在实验库中定位' : '去实验库补标准模板'}</button>
          <button class="secondary" type="button" onclick="${dashboard ? `showDashboard('${esc(dashboard.path)}')` : 'void(0)'}" ${dashboard ? '' : 'disabled'}>查看结果</button>
        </div>
      `;
    }

    function activePlanRecord() {
      const matched = [...(state.workspace?.experiments || [])].find(plan => plan.path === state.currentExperiment) || null;
      if (!matched && !state.currentExperimentMapping) return null;
      if (!matched) {
        return {
          path: state.currentExperiment || '',
          name: state.currentExperimentMapping?.metadata?.name || '当前计划',
          scenario: state.currentExperimentMapping?.scenario || '—',
          runs: editorPlanMeta(state.currentExperimentMapping).runs,
          sweeps: editorPlanMeta(state.currentExperimentMapping).sweeps,
          monte_carlo_samples: editorPlanMeta(state.currentExperimentMapping).monteCarlo,
          description: state.currentExperimentMapping?.metadata?.description || '',
        };
      }
      return {
        ...matched,
        name: state.currentExperimentMapping?.metadata?.name || matched.name,
        scenario: state.currentExperimentMapping?.scenario || matched.scenario,
        description: state.currentExperimentMapping?.metadata?.description || matched.description,
      };
    }

    function representativeDashboardForExperiment(experimentId, dashboards = null) {
      const config = curatedExperimentConfig(experimentId);
      if (!config) return null;
      const rows = [...(dashboards || state.workspace?.dashboards || [])].sort((a, b) => Number(b.updated_ts || 0) - Number(a.updated_ts || 0));
      const planPath = resolveCuratedExperimentPlan(config);
      if (planPath) {
        const plan = [...(state.workspace?.experiments || [])].find(item => item.path === planPath);
        const matched = matchingDashboardsForPlan(plan, rows)[0] || null;
        if (matched) return matched;
      }
      const planName = normalizedAssetName(config.planName || '');
      const scenario = resolveCuratedExperimentScenario(config);
      const candidates = rows
        .map(item => {
          let score = 0;
          const name = normalizedAssetName(item.name || '');
          const folder = dashboardFolderName(item);
          const path = String(item.path || '').toLowerCase().replace(/\\\\/g, '/');
          if (planName) {
            if (name === planName) score = Math.max(score, 140);
            if (folder === planName) score = Math.max(score, 135);
            if (path.includes(`/${planName}/dashboard.html`)) score = Math.max(score, 132);
            if (path.includes(`/${planName}_`)) score = Math.max(score, 120);
            if (name.includes(planName) || folder.includes(planName)) score = Math.max(score, 105);
          }
          if (scenario && item.scenario === scenario) score += 6;
          return {item, score};
        })
        .filter(entry => entry.score > 0)
        .sort((left, right) =>
          right.score - left.score
          || Number(right.item.acceptance_rate || 0) - Number(left.item.acceptance_rate || 0)
          || Number(left.item.best_final_error_deg ?? Number.POSITIVE_INFINITY) - Number(right.item.best_final_error_deg ?? Number.POSITIVE_INFINITY)
          || Number(right.item.updated_ts || 0) - Number(left.item.updated_ts || 0)
        );
      return candidates[0]?.item || rows.find(item => item.scenario === scenario) || null;
    }

    function representativeDashboardSummary(dashboard) {
      if (!dashboard) return '当前还没有代表结果。';
      const rate = `${Math.round(Number(dashboard.acceptance_rate || 0) * 1000) / 10}%`;
      return `通过率 ${rate}，最佳 run ${dashboard.best_run_id || '—'}，最佳末端误差 ${fmt(dashboard.best_final_error_deg)} deg。`;
    }

    function renderRecentExperimentPlans() {
      const rows = filteredExperiments();
      if (!rows.length) {
        recentExperimentPlans.innerHTML = `
          <div class="callout" style="margin-bottom:0">
            <strong>最近实验计划</strong>
            <p>${(state.experimentFilter.trim() || state.experimentStatusFilter !== 'all') ? '当前筛选条件下没有匹配的实验计划。可以清空筛选、切换状态或换个关键词。' : '还没有可管理的实验计划。你可以先用上面的创建器生成一个。'}</p>
          </div>
        `;
        return;
      }
      const cards = rows.slice(0, 3).map(plan => {
        const isCreated = plan.path === state.builderLastCreated?.path;
        const isEditing = plan.path === state.currentExperiment;
        const badge = isCreated ? '刚创建' : isEditing ? '当前编辑' : '最近计划';
        const scale = plan.error ? '计划异常' : `${plan.sweeps ? '参数扫描' : '单场景'} · MC ${plan.monte_carlo_samples || 0}`;
        const dashboard = matchingDashboardForPlan(plan);
        return `
          <button class="history-card ${isCreated || isEditing ? 'active' : ''}" type="button" onclick="showExperiment('${esc(plan.path)}')">
            <span>${esc(badge)}</span>
            <strong>${esc(plan.name)}</strong>
            <p>${esc(plan.scenario || '—')} · ${esc(scale)}${dashboard ? ' · 已有结果' : ''}<br>${esc(plan.updated_at || '')}</p>
          </button>
        `;
      }).join('');
      const detail = rows.find(plan => plan.path === state.builderLastCreated?.path)
        || rows.find(plan => plan.path === state.currentExperiment)
        || rows[0];
      const detailScale = detail.error ? detail.error : `${detail.sweeps ? '参数扫描' : '单场景'} · MC ${detail.monte_carlo_samples || 0}`;
      const detailDashboard = matchingDashboardForPlan(detail);
      recentExperimentPlans.innerHTML = `
        <div class="history-timeline">
          <h3>最近实验计划</h3>
          <div class="history-grid">${cards}</div>
          <div class="history-detail" style="margin-bottom:0">
            <strong>${esc(detail.name)} · ${detail.path === state.builderLastCreated?.path ? '刚创建计划' : detail.path === state.currentExperiment ? '当前编辑计划' : '最近更新计划'}</strong>
            <p>场景 ${esc(detail.scenario || '—')}，规模 ${esc(detailScale)}，计划文件 ${esc(detail.path)}，最近更新时间 ${esc(detail.updated_at || '—')}。${detailDashboard ? ` 已匹配结果 ${esc(detailDashboard.name)}。` : ' 还没有找到对应结果，可以直接运行。'} </p>
            <div class="toolbar" style="margin-top:10px">
              <button type="button" onclick="showExperiment('${esc(detail.path)}')">编辑计划</button>
              <button class="secondary" type="button" onclick="duplicateExperiment('${esc(detail.path)}')">复制计划</button>
              <button class="secondary" type="button" onclick="renameExperiment('${esc(detail.path)}')">重命名计划</button>
              <button class="secondary" type="button" onclick="archiveExperiment('${esc(detail.path)}')">归档计划</button>
              <button class="secondary" type="button" onclick="validatePlan('${esc(detail.path)}')">校验计划</button>
              <button class="secondary" type="button" onclick="runPlan('${esc(detail.path)}')">运行计划</button>
              <button class="secondary" type="button" onclick="${detailDashboard ? `showDashboard('${esc(detailDashboard.path)}')` : 'void(0)'}" ${detailDashboard ? '' : 'disabled'}>查看对应结果</button>
            </div>
          </div>
        </div>
      `;
    }

    function renderExperimentPicker() {
      if (!experimentPicker) return;
      const rows = [...(state.workspace?.experiments || [])].sort((a, b) => Number(b.updated_ts || 0) - Number(a.updated_ts || 0));
      if (!rows.length) {
        experimentPicker.innerHTML = `
          <div class="history-detail" style="margin-bottom:0">
            <strong>当前还没有可选实验</strong>
            <p>你可以先切到“创建实验”生成一个新计划，或者去“实验库”载入推荐实验模板。</p>
            <div class="toolbar" style="margin-top:10px">
              <button type="button" onclick="switchLabView('builder')">去创建实验</button>
              <button class="secondary" type="button" onclick="switchLabView('library')">查看推荐实验</button>
            </div>
          </div>
        `;
        renderManageWorkbenchSummary();
        return;
      }
      const selectedPath = selectedEditorQuickPath();
      state.editorQuickPickPath = selectedPath;
      const selectedPlan = rows.find(plan => plan.path === selectedPath) || rows[0];
      const selectedDashboard = matchingDashboardForPlan(selectedPlan);
      const selectedProfile = planAssetProfile(selectedPlan);
      const selectedResearch = planResearchMeta(selectedPlan);
      const selectedResultText = selectedDashboard
        ? `已联动 ${selectedDashboard.name}，通过率 ${percentText(selectedDashboard.acceptance_rate)}。`
        : '当前还没有对应结果，适合先校验并运行。';
      const selectedNext = selectedDashboard
        ? '先去结果总览继续读图、对比或回放。'
        : '先载入到编辑器，再补变量或验收后运行。';
      const cards = rows.slice(0, 6).map(plan => {
        const dashboard = matchingDashboardForPlan(plan);
        const active = plan.path === state.currentExperiment;
        const badge = active ? '当前编辑' : (plan.path === state.builderLastCreated?.path ? '刚创建' : '最近实验');
        const scale = experimentScaleText(plan);
        const profile = planAssetProfile(plan);
        return `
          <div class="picker-card ${active ? 'active' : ''}">
            <span>${esc(badge)}</span>
            <strong>${esc(plan.name)}</strong>
            <p>场景 ${esc(plan.scenario || '—')} · ${esc(scale)}<br>${esc(profile.line)} · ${esc(profile.status)}<br>计划文件 ${esc(plan.path)}</p>
            <div class="toolbar" style="margin-top:10px; margin-bottom:0">
              <button type="button" onclick="showExperiment('${esc(plan.path)}')">载入编辑</button>
              <button class="secondary" type="button" onclick="runPlan('${esc(plan.path)}')">运行</button>
              <button class="secondary" type="button" onclick="${dashboard ? `showDashboard('${esc(dashboard.path)}')` : 'void(0)'}" ${dashboard ? '' : 'disabled'}>结果</button>
            </div>
          </div>
        `;
      }).join('');
      experimentPicker.innerHTML = `
          <div class="history-detail" style="margin-bottom:12px">
            <div class="selector-summary">
              <strong>最近可选实验</strong>
              <span>当前共 ${rows.length} 个计划。先在这里选中一个，再载入到右侧编辑器。</span>
            </div>
          <div class="quick-select-grid">
            <select id="manage-top-quick-select" onchange="setEditorQuickPick(this.value)">
              ${experimentOptionMarkup(rows, selectedPath)}
            </select>
            <button type="button" onclick="loadSelectedEditorExperiment()">载入编辑</button>
            <button class="secondary" type="button" onclick="runSelectedEditorExperiment()">运行</button>
            <button class="secondary" type="button" onclick="openSelectedEditorExperimentResult()" ${selectedDashboard ? '' : 'disabled'}>查看结果</button>
          </div>
            <div class="quick-select-note">
              当前高亮 <strong>${esc(selectedPlan.name)}</strong>，场景 ${esc(selectedPlan.scenario || '—')}，${esc(experimentScaleText(selectedPlan))}。
            </div>
            <div class="summary-grid" style="margin-top:10px">
              <div class="summary-card"><span>实验主线</span><strong>${esc(selectedProfile.line)}</strong></div>
              <div class="summary-card"><span>资产状态</span><strong>${esc(selectedProfile.status)}</strong></div>
              <div class="summary-card"><span>结果状态</span><strong>${esc(selectedDashboard ? '已有结果' : '待运行')}</strong></div>
              <div class="summary-card"><span>推荐动作</span><strong>${esc(selectedDashboard ? '去结果层' : '先进入编辑器')}</strong></div>
            </div>
            <div class="detail-grid" style="margin-top:10px">
              <div class="detail-box"><strong>当前研究问题</strong><div>${esc(selectedResearch.profile.question || '当前计划用于组织一组可复现实验。')}</div></div>
              <div class="detail-box"><strong>结果联动</strong><div>${esc(selectedResultText)}</div></div>
              <div class="detail-box"><strong>实验价值</strong><div>${esc(selectedResearch.detail.platform || '当前计划适合继续沉淀成稳定实验资产。')}</div></div>
              <div class="detail-box"><strong>下一步</strong><div>${esc(selectedNext)}</div></div>
            </div>
          </div>
        <div class="picker-grid">${cards}</div>
      `;
      renderManageWorkbenchSummary();
    }

    function renderManageWorkbenchSummary() {
      if (!manageWorkbenchSummary) return;
      const allRows = [...(state.workspace?.experiments || [])].sort((a, b) => Number(b.updated_ts || 0) - Number(a.updated_ts || 0));
      if (!allRows.length) {
        manageWorkbenchSummary.innerHTML = `
          <div class="callout" style="margin-bottom:0">
            <strong>计划管理工作台</strong>
            <p>当前还没有实验计划。建议先去“实验库”挑一个成熟模板，或切到“创建实验”生成你的第一个计划。</p>
          </div>
        `;
        return;
      }
      const filtered = filteredExperiments();
      const selectedRows = selectedExperimentRows();
      const currentPlan = allRows.find(plan => plan.path === state.currentExperiment) || null;
      const focusPlan = currentPlan || filtered[0] || allRows[0];
      const focusDashboard = matchingDashboardForPlan(focusPlan);
      const profile = planAssetProfile(focusPlan);
      const research = planResearchMeta(focusPlan);
      const representativeAsset = representativeAssetRecord({plan: focusPlan, line: profile.line});
      const assetBinding = standardAssetBindingRecord({plan: focusPlan});
      const assetSummary = state.workspace?.asset_summary || {};
      const withResults = allRows.filter(plan => matchingDashboardForPlan(plan)).length;
      const focusExperimentId = research.currentId || assetBinding.experimentId || representativeAsset.experimentId || '';
      const focusConfig = research.config || (focusExperimentId ? curatedExperimentConfig(focusExperimentId) : null);
      const focusAcademic = research.academic || experimentAcademicMeta(focusExperimentId, focusConfig || {planName: focusPlan.name, theme: profile.line});
      const focusStandard = research.standard || experimentStandardCardMeta(
        focusExperimentId,
        focusConfig || {planName: focusPlan.name, theme: profile.line},
        focusAcademic,
      );
      const focusMapping = currentPlan?.path === focusPlan.path ? state.currentExperimentMapping : null;
      const focusSweepRows = focusMapping ? planSweepSummaryRows(focusMapping) : [];
      const focusAcceptanceRows = focusMapping ? acceptanceProfileRows(focusMapping) : [];
      const focusBrief = experimentBriefMeta({
        experimentId: focusExperimentId,
        config: focusConfig || {planName: focusPlan.name, theme: profile.line},
        detailMeta: research.detail,
        academicMeta: focusAcademic,
        standardMeta: focusStandard,
        theme: focusConfig?.theme || profile.line,
        question: research.profile.question || profile.question,
        scenarioPath: focusPlan.scenario,
        taskSummary: `${profile.line} · ${profile.status}。${research.detail.platform || '当前计划处于平台主线中的一个可复用环节。'}`
          + (focusMapping ? ` ${planReadinessText(focusMapping, focusDashboard)}` : ''),
        sweepRows: focusSweepRows,
        acceptanceRows: focusAcceptanceRows,
        mcSamples: Number(focusMapping?.monte_carlo?.samples || 0),
        acceptanceSummary: focusAcceptanceRows.length ? '' : focusStandard.acceptance,
        figuresSummary: focusStandard.figurePackage,
        nextSummary: profile.next,
      });
      const focusReadingTask = experimentReadingTaskMeta({
        experimentId: focusExperimentId,
        config: focusConfig || {planName: focusPlan.name, theme: profile.line},
        detailMeta: research.detail,
        academicMeta: focusAcademic,
        standardMeta: focusStandard,
        theme: focusConfig?.theme || profile.line,
        nextAction: focusDashboard
          ? '已有结果，优先去结果总览继续读图、对比和回放，再决定是否复制一版做下一轮实验。'
          : '先校验并运行，形成 README、index.json、summary_metrics.csv 和 dashboard.html 这一套标准结果产物。',
        deliverables: focusDashboard
          ? 'README.md、index.json、summary_metrics.csv、dashboard.html'
          : focusStandard.figurePackage,
      });
      const poolPinnedWorkbenchView = state.manageView === 'pool' && ['roadmap', 'assets'].includes(state.managePoolView)
        ? state.managePoolView
        : '';
      const workbenchView = poolPinnedWorkbenchView
        || (['focus', 'roadmap', 'assets', 'actions'].includes(state.manageWorkbenchView) ? state.manageWorkbenchView : 'focus');
      state.manageWorkbenchView = workbenchView;
      const showWorkbenchTabs = !poolPinnedWorkbenchView;
      const nextAction = currentPlan
        ? (focusDashboard
            ? '当前计划已有结果，适合继续编辑、复跑，或直接切到“结果总览”做对比与回放。'
            : '当前计划还没有结果，建议先校验，再运行生成第一版实验记录。')
        : '先从上面的实验选择器载入一个计划，再到右侧编辑器做修改、复制或归档。';
      const duplicationCards = (assetSummary.duplication_candidates || []).slice(0, 3).map(item => `
        <div class="detail-box">
          <strong>${esc(item.family)}</strong>
          <div>${esc(item.count)} 个同族计划：${esc((item.names || []).join('、'))}</div>
        </div>
      `).join('');
      const actionNodes = [
        {
          tone: currentPlan ? 'accent' : '',
          step: 'A. 选计划',
          title: currentPlan?.name || focusPlan.name,
          detail: currentPlan ? '当前已载入编辑器，可直接修改、重命名或复制。' : '先从实验列表中选一个焦点计划进入编辑器。',
        },
        {
          tone: 'warm',
          step: 'B. 看状态',
          title: profile.status,
          detail: focusDashboard
            ? `已形成结果 ${focusDashboard.name}，可以回到结果页继续读图。`
            : '当前还没有结果，建议先校验计划与输出目录。',
        },
        {
          tone: focusDashboard ? 'ok' : 'warm',
          step: 'C. 做动作',
          title: focusDashboard ? '复跑 / 对比 / 回放' : '保存 / 校验 / 运行',
          detail: nextAction,
        },
      ];
      const roadmapRecords = protocolWorkspaceMapRecords({
        dashboards: state.workspace?.dashboards || [],
        plans: allRows,
      });
      const roadmapFocusProtocolId = standardAssetBindingRecord({plan: focusPlan}).protocolId || representativeAsset.protocolId || '';
      const priorityStages = standardRouteStageRecords();
      const focusRouteStage = priorityStages.find(stage => stage.experimentId === focusExperimentId) || null;
      const priorityMissingResult = priorityStages.find(stage => stage.plan && !stage.result) || null;
      const priorityMissingPlan = priorityStages.find(stage => !stage.plan) || null;
      const priorityShowcase = priorityStages.find(stage => stage.id === 'mission_sequence') || priorityStages.find(stage => stage.id === 'actuator_margin') || priorityStages[priorityStages.length - 1] || null;
      const selectedPriorityCards = [
        {
          title: '当前焦点实验',
          tone: 'accent',
          stage: focusRouteStage,
          note: '这就是当前计划所在的主线位置，适合先围绕它判断是否继续编辑、复跑或切到结果层。',
        },
        {
          title: priorityMissingResult ? '优先补代表结果' : '优先补代表计划',
          tone: priorityMissingResult ? 'warm' : 'accent',
          stage: priorityMissingResult || priorityMissingPlan,
          note: priorityMissingResult
            ? '这条主线已有计划但还缺代表结果，优先补出稳定 dashboard 才能把主线讲完整。'
            : '这条主线还没有稳定计划，适合先从实验库载入模板或在创建器生成代表计划。',
        },
        {
          title: '优先展示入口',
          tone: 'ok',
          stage: priorityShowcase,
          note: '做演示或汇报时，优先从这一环切入，更容易把任务流程、环境和平台价值讲清楚。',
        },
      ].filter(item => item.stage);
      const priorityCardsMarkup = (() => {
        const cards = [];
        const seen = new Set();
        for (const item of selectedPriorityCards) {
          if (seen.has(item.stage.id)) continue;
          seen.add(item.stage.id);
          cards.push(item);
        }
        return cards.map(item => `
          <div class="flow-node ${esc(item.tone)}">
            <small>${esc(item.title)}</small>
            <strong>${esc(item.stage.step)} · ${esc(item.stage.label)}</strong>
            <p>${esc(item.stage.status)}。${esc(item.note)}</p>
          </div>
        `).join('');
      })();
      const focusMarkup = `
        <div class="history-detail" style="margin:10px 0 0">
          <strong>计划推进路径</strong>
          <p>先锁定计划，再判断结果状态，最后决定继续编辑还是进入结果层。</p>
          <div class="flow-sequence" style="margin-top:10px">
            ${actionNodes.map(node => `
              <div class="flow-node ${esc(node.tone)}">
                <small>${esc(node.step)}</small>
                <strong>${esc(node.title)}</strong>
                <p>${esc(node.detail)}</p>
              </div>
            `).join('')}
          </div>
        </div>
        <div class="detail-grid" style="margin-top:10px">
          <div class="detail-box">
            <strong>当前焦点计划</strong>
            <div>${esc(focusPlan.name)} · 场景 ${esc(focusPlan.scenario || '—')} · ${esc(experimentScaleText(focusPlan))}</div>
          </div>
          <div class="detail-box">
            <strong>结果状态</strong>
            <div>${focusDashboard ? `已匹配结果 ${esc(focusDashboard.name)}，通过率 ${Math.round(Number(focusDashboard.acceptance_rate || 0) * 1000) / 10}%。` : '还没有对应结果，适合先做校验或首轮运行。'}</div>
          </div>
          <div class="detail-box">
            <strong>实验主线</strong>
            <div>${esc(profile.line)} · ${esc(profile.status)}</div>
          </div>
          <div class="detail-box">
            <strong>当前研究问题</strong>
            <div>${esc(profile.question)}</div>
          </div>
        </div>
        <div class="summary-grid" style="margin-top:10px">
          <div class="summary-card"><span>当前编辑</span><strong>${esc(currentPlan?.name || '未载入')}</strong></div>
          <div class="summary-card"><span>焦点状态</span><strong>${esc(focusDashboard ? '已有结果' : '待首轮运行')}</strong></div>
          <div class="summary-card"><span>主线位置</span><strong>${esc(profile.line)}</strong></div>
          <div class="summary-card"><span>推荐动作</span><strong>${esc(focusDashboard ? '回结果层' : '先校验并运行')}</strong></div>
        </div>
        <div class="callout" style="margin:10px 0 0">
          <strong>实验资产定位</strong>
          <p>${esc(profile.next)}</p>
          <div class="toolbar" style="margin-top:10px">
            <button type="button" onclick="switchManageView('editor')">去当前计划</button>
            <button class="secondary" type="button" onclick="${focusDashboard ? `showDashboard('${esc(focusDashboard.path)}')` : 'void(0)'}" ${focusDashboard ? '' : 'disabled'}>看当前结果</button>
            <button class="secondary" type="button" onclick="switchManageView('history')">看版本与归档</button>
            <button class="secondary" type="button" onclick="${profile.curated ? `focusExperimentLibrary('${esc(profile.curatedId)}')` : `switchLabView('library')`}">${profile.curated ? '在实验库中定位' : '去实验库补模板'}</button>
          </div>
        </div>
      `;
      const roadmapMarkup = `
        <div class="history-detail" style="margin:10px 0 0">
          <strong>主线与协议看板</strong>
          <p>计划管理里单独保留这张看板，用来回答“当前工作区已经覆盖到哪一步、哪条协议还缺计划或缺结果”。</p>
          <div class="summary-grid" style="margin-top:10px">
            <div class="summary-card"><span>协议阶段</span><strong>${roadmapRecords.length}</strong></div>
            <div class="summary-card"><span>当前焦点协议</span><strong>${esc((experimentProtocolConfig(roadmapFocusProtocolId)?.label) || '未映射')}</strong></div>
            <div class="summary-card"><span>已有计划协议</span><strong>${roadmapRecords.filter(record => record.workspacePlans.length).length}</strong></div>
            <div class="summary-card"><span>已有结果协议</span><strong>${roadmapRecords.filter(record => record.workspaceResults).length}</strong></div>
          </div>
        </div>
        <div class="intro-grid" style="margin-top:10px">
          ${roadmapRecords.map(record => protocolWorkspaceCardHtml(record, {
            mode: 'manage',
            focusProtocolId: roadmapFocusProtocolId,
          })).join('')}
        </div>
      `;
      const assetsMarkup = `
        <div class="history-detail" style="margin:10px 0 0">
          <strong>实验资产健康</strong>
          <p>把标准资产、样例和副本分开看，避免实验库和工作区失去主线。</p>
          <div class="summary-grid" style="margin-top:10px">
            <div class="summary-card"><span>标准资产</span><strong>${fmt(assetSummary.library_count || 0)}</strong></div>
            <div class="summary-card"><span>演示样例</span><strong>${fmt(assetSummary.showcase_count || 0)}</strong></div>
            <div class="summary-card"><span>平台生成</span><strong>${fmt(assetSummary.generated_count || 0)}</strong></div>
            <div class="summary-card"><span>待收敛副本</span><strong>${fmt(assetSummary.needs_consolidation_count || 0)}</strong></div>
          </div>
          <div class="detail-grid" style="margin-top:10px">
            <div class="detail-box"><strong>当前计划资产类型</strong><div>${esc(profile.assetLabel || profile.status)} · 资产族 ${esc(profile.assetFamily || focusPlan.name)}</div></div>
            <div class="detail-box"><strong>整理建议</strong><div>${esc(profile.needsConsolidation ? '当前计划属于同族副本。建议保留代表计划后，把历史副本归档，避免后续结果入口继续分叉。' : '当前计划资产定位较清晰，可以继续补结果、沉淀代表结论或再派生下一轮实验。')}</div></div>
          </div>
          <div class="detail-grid" style="margin-top:10px">
            ${duplicationCards || '<div class="detail-box"><strong>同族副本</strong><div>当前没有明显需要收敛的同族实验副本，实验资产结构比较整洁。</div></div>'}
          </div>
        </div>
        <div class="history-detail" style="margin:10px 0 0">
          <strong>主线代表资产</strong>
          <p>把当前计划放回所属实验主线，直接判断代表计划、代表结果和下一步动作。</p>
          <div class="detail-grid" style="margin-top:10px">
            <div class="detail-box">
              <strong>代表计划</strong>
              <div>${representativeAsset.plan ? `${esc(representativeAsset.plan.name)} · ${esc(experimentScaleText(representativeAsset.plan))}` : '当前主线还没有稳定代表计划。'}</div>
            </div>
            <div class="detail-box">
              <strong>代表结果</strong>
              <div>${representativeAsset.dashboard ? `${esc(representativeAsset.dashboard.name)} · ${esc(representativeDashboardSummary(representativeAsset.dashboard))}` : '当前主线还没有稳定代表结果。'}</div>
            </div>
            <div class="detail-box">
              <strong>主线阶段</strong>
              <div>${esc(representativeAsset.status)} · ${esc(representativeAsset.standardMeta.maturity)}</div>
            </div>
            <div class="detail-box">
              <strong>当前计划与主线关系</strong>
              <div>${esc(representativeAsset.plan?.path === focusPlan.path ? '当前焦点计划就是这条主线的代表计划。' : representativeAsset.next)}</div>
            </div>
          </div>
          <div class="toolbar" style="margin-top:10px">
            <button class="secondary" type="button" onclick="${representativeAsset.plan ? `showExperiment('${esc(representativeAsset.plan.path)}')` : 'void(0)'}" ${representativeAsset.plan ? '' : 'disabled'}>打开主线代表计划</button>
            <button class="secondary" type="button" onclick="${representativeAsset.dashboard ? `showDashboard('${esc(representativeAsset.dashboard.path)}')` : 'void(0)'}" ${representativeAsset.dashboard ? '' : 'disabled'}>打开主线代表结果</button>
            <button class="secondary" type="button" onclick="${representativeAsset.experimentId ? `focusExperimentLibrary('${esc(representativeAsset.experimentId)}')` : `switchLabView('library')`}">${representativeAsset.experimentId ? '在实验库定位主线' : '去实验库补主线'}</button>
          </div>
        </div>
        ${standardAssetBindingHtml(assetBinding, {title: '当前计划的标准资产绑定'})}
        <details class="fold-panel" style="margin-top:10px">
          <summary>展开标准实验主线看板<span>计划管理也按同一条主线判断当前计划位于哪一环</span></summary>
          <div class="fold-panel-body">
            ${standardRouteWorkbenchHtml(standardRouteStageRecords(), {
              mode: 'manage',
              title: '计划管理主线看板',
              lead: '计划管理不再只是文件编辑区。这里也共用实验库和结果页的主线路线板，方便直接判断当前计划和代表资产的关系。',
              focusExperimentId: representativeAsset.experimentId || profile.curatedId || '',
            })}
          </div>
        </details>
      `;
      const actionsMarkup = `
        <div class="history-detail" style="margin:10px 0 0">
          <strong>计划操作路径</strong>
          <p>把管理动作压成一条短路径，直接判断当前该编辑、运行还是回结果。</p>
          <div class="flow-sequence" style="margin-top:10px">
            ${actionNodes.map(node => `
              <div class="flow-node ${esc(node.tone)}">
                <small>${esc(node.step)}</small>
                <strong>${esc(node.title)}</strong>
                <p>${esc(node.detail)}</p>
              </div>
            `).join('')}
          </div>
        </div>
        <div class="callout info" style="margin:10px 0 0">
          <strong>推荐下一步</strong>
          <p>${esc(nextAction)}</p>
          <div class="toolbar" style="margin-top:10px">
            <button type="button" onclick="switchManageView('editor')">${currentPlan ? '进入编辑器' : '去载入计划'}</button>
            <button class="secondary" type="button" onclick="${focusDashboard ? `showDashboard('${esc(focusDashboard.path)}')` : 'void(0)'}" ${focusDashboard ? '' : 'disabled'}>查看当前结果</button>
            <button class="secondary" type="button" onclick="switchManagePoolView('plans')">去计划清单</button>
          </div>
        </div>
        <div class="history-detail" style="margin:10px 0 0">
          <strong>当前建议的实验补强方向</strong>
          <p>计划管理页也直接给出主线优先级，避免只知道“操作这个计划”，却不知道整个平台下一步该补哪类实验。</p>
          <div class="flow-sequence" style="margin-top:10px">
            ${priorityCardsMarkup || '<div class="detail-box"><strong>主线优先级</strong><div>当前还没有足够的主线资产来判断优先级。</div></div>'}
          </div>
        </div>
      `;
      const semanticsMarkup = `
        ${experimentBriefHtml(focusBrief, {
          title: '当前计划实验说明卡',
          lead: '计划管理也按实验库的 7 项语义来读当前计划，避免这里只剩文件名、路径和按钮。',
          compact: true,
        })}
        ${experimentReadingTaskHtml(focusReadingTask, {
          title: '当前计划结果阅读任务书',
          lead: '这里直接告诉你这份计划跑完后先看什么、再去哪里、报告最该抓什么。',
          compact: true,
        })}
        ${standardAssetBindingHtml(assetBinding, {
          title: '当前计划主线资产链',
          showToolbar: false,
        })}
      `;
      manageWorkbenchSummary.innerHTML = `
        <div class="history-detail" style="margin-bottom:0">
          <strong>计划管理工作台</strong>
          <p>把“选计划、看状态、做动作”收在一处。</p>
          <div class="summary-grid" style="margin-top:10px">
            <div class="summary-card"><span>当前编辑</span><strong>${esc(currentPlan?.name || '未载入')}</strong></div>
            <div class="summary-card"><span>筛选结果</span><strong>${filtered.length}</strong></div>
            <div class="summary-card"><span>已选择计划</span><strong>${selectedRows.length}</strong></div>
            <div class="summary-card"><span>已有结果计划</span><strong>${withResults}</strong></div>
          </div>
          <div class="editor-view-shell" style="margin-top:10px">
            <div class="segment-control" ${showWorkbenchTabs ? '' : 'hidden'}>
              <button class="${workbenchView === 'focus' ? 'active' : ''}" type="button" onclick="switchManageWorkbenchView('focus')">计划焦点</button>
              <button class="${workbenchView === 'roadmap' ? 'active' : ''}" type="button" onclick="switchManageWorkbenchView('roadmap')">主线看板</button>
              <button class="${workbenchView === 'assets' ? 'active' : ''}" type="button" onclick="switchManageWorkbenchView('assets')">资产主线</button>
              <button class="${workbenchView === 'actions' ? 'active' : ''}" type="button" onclick="switchManageWorkbenchView('actions')">下一步动作</button>
            </div>
            <div class="editor-view-note">${esc(poolPinnedWorkbenchView ? `当前由上层工作区锁定在“${workbenchView === 'roadmap' ? '主线看板' : '资产主线'}”，减少页面内重复切换。` : manageWorkbenchSummaryText(workbenchView))}</div>
          </div>
          <div class="workbench-hero-grid" style="margin-top:10px">
            <div>
              <div class="workspace-panel-view" data-manage-workbench-view="focus" ${workbenchView === 'focus' ? '' : 'hidden'}>${focusMarkup}</div>
              <div class="workspace-panel-view" data-manage-workbench-view="roadmap" ${workbenchView === 'roadmap' ? '' : 'hidden'}>${roadmapMarkup}</div>
              <div class="workspace-panel-view" data-manage-workbench-view="assets" ${workbenchView === 'assets' ? '' : 'hidden'}>${assetsMarkup}</div>
              <div class="workspace-panel-view" data-manage-workbench-view="actions" ${workbenchView === 'actions' ? '' : 'hidden'}>${actionsMarkup}</div>
            </div>
            <div class="section-stack">
              ${semanticsMarkup}
            </div>
          </div>
        </div>
      `;
    }

    function renderExperimentListSwitcher() {
      if (!experimentListSwitcher) return;
      const preferredRows = filteredExperiments();
      const rows = preferredRows.length
        ? preferredRows
        : [...(state.workspace?.experiments || [])].sort((a, b) => Number(b.updated_ts || 0) - Number(a.updated_ts || 0));
      if (!rows.length) {
        experimentListSwitcher.innerHTML = `
          <div class="quick-select-panel" style="margin:0 0 10px">
            <strong>实验切换</strong>
            <p>当前还没有可选实验计划。可以先去“创建实验”生成一个，或在“实验库”载入推荐模板。</p>
          </div>
        `;
        return;
      }
      const selectedPath = selectedEditorQuickPath();
      state.editorQuickPickPath = selectedPath;
      const selectedPlan = rows.find(plan => plan.path === selectedPath) || rows[0];
      const selectedDashboard = matchingDashboardForPlan(selectedPlan);
      experimentListSwitcher.innerHTML = `
        <div class="quick-select-panel" style="margin:0 0 10px">
          <div class="selector-summary">
            <strong>实验切换</strong>
            <span>这里直接显示当前筛选范围内的计划选择器。即使上面的卡片没有看到，也能从这里切换。</span>
          </div>
          <div class="quick-select-grid">
            <select id="manage-list-quick-select" onchange="setEditorQuickPick(this.value)">
              ${experimentOptionMarkup(rows, selectedPath)}
            </select>
            <button type="button" onclick="loadSelectedEditorExperiment()">载入编辑</button>
            <button class="secondary" type="button" onclick="runSelectedEditorExperiment()">运行</button>
            <button class="secondary" type="button" onclick="openSelectedEditorExperimentResult()" ${selectedDashboard ? '' : 'disabled'}>查看结果</button>
          </div>
          <div class="quick-select-note">
            当前选中 <strong>${esc(selectedPlan.name)}</strong>。如果想新建计划，请切到“创建实验”；如果想套用成熟模板，请切到“实验库”。
          </div>
        </div>
      `;
    }

    function selectedEditorQuickPath() {
      const rows = [...(state.workspace?.experiments || [])].sort((a, b) => Number(b.updated_ts || 0) - Number(a.updated_ts || 0));
      if (!rows.length) return '';
      const picker = document.getElementById('editor-quick-select');
      const preferred = picker?.value || state.currentExperiment || state.editorQuickPickPath || rows[0].path;
      return rows.some(plan => plan.path === preferred) ? preferred : rows[0].path;
    }

    function setEditorQuickPick(path) {
      state.editorQuickPickPath = path || '';
      renderExperimentPicker();
      renderExperimentListSwitcher();
      renderEditorQuickLoad();
    }

    async function loadSelectedEditorExperiment() {
      const path = selectedEditorQuickPath();
      if (path) {
        state.editorQuickPickPath = path;
        await showExperiment(path);
      }
    }

    async function runSelectedEditorExperiment() {
      const path = selectedEditorQuickPath();
      if (path) {
        state.editorQuickPickPath = path;
        await runPlan(path);
      }
    }

    function openSelectedEditorExperimentResult() {
      const path = selectedEditorQuickPath();
      const plan = [...(state.workspace?.experiments || [])].find(item => item.path === path);
      const dashboard = matchingDashboardForPlan(plan);
      if (dashboard) {
        showDashboard(dashboard.path);
      }
    }

    function renderEditorQuickLoad() {
      if (!editorQuickLoad) return;
      const rows = [...(state.workspace?.experiments || [])].sort((a, b) => Number(b.updated_ts || 0) - Number(a.updated_ts || 0));
      if (!rows.length) {
        editorQuickLoad.innerHTML = `
          <div class="quick-select-panel">
            <strong>快速选择实验计划</strong>
            <p>当前还没有可载入的实验计划。可以先去“创建实验”生成一个，或在“实验库”里载入推荐模板。</p>
          </div>
        `;
        return;
      }
      const selectedPath = selectedEditorQuickPath();
      state.editorQuickPickPath = selectedPath;
      const selectedPlan = rows.find(plan => plan.path === selectedPath) || rows[0];
      const selectedDashboard = matchingDashboardForPlan(selectedPlan);
      const scale = experimentScaleText(selectedPlan);
      editorQuickLoad.innerHTML = `
        <div class="quick-select-panel">
          <div class="selector-summary">
            <strong>快速选择实验计划</strong>
            <span>当前编辑区共有 ${rows.length} 个可切换计划。这里始终保留选择器，避免来回滚动页面。</span>
          </div>
          <div class="quick-select-grid">
            <select id="editor-quick-select" onchange="setEditorQuickPick(this.value)">
              ${experimentOptionMarkup(rows, selectedPath)}
            </select>
            <button type="button" onclick="loadSelectedEditorExperiment()">载入编辑</button>
            <button class="secondary" type="button" onclick="runSelectedEditorExperiment()">运行</button>
            <button class="secondary" type="button" onclick="openSelectedEditorExperimentResult()" ${selectedDashboard ? '' : 'disabled'}>查看结果</button>
          </div>
          <div class="quick-select-note">
            当前选中 <strong>${esc(selectedPlan.name)}</strong>，场景 ${esc(selectedPlan.scenario || '—')}，${esc(scale)}，计划文件 ${esc(selectedPlan.path)}。
          </div>
        </div>
      `;
    }

    function renderArchivedExperimentPlans() {
      const rows = [...(state.workspace?.archived_experiments || [])].sort((a, b) => Number(b.updated_ts || 0) - Number(a.updated_ts || 0));
      if (!rows.length) {
        archivedExperimentPlans.innerHTML = `
          <div class="callout" style="margin-bottom:0">
            <strong>已归档计划</strong>
            <p>当前没有已归档计划。归档后的计划会显示在这里，便于后续恢复。</p>
          </div>
        `;
        return;
      }
      const cards = rows.slice(0, 3).map(plan => {
        const scale = plan.error ? '计划异常' : `${plan.sweeps ? '参数扫描' : '单场景'} · MC ${plan.monte_carlo_samples || 0}`;
        return `
          <button class="history-card" type="button" onclick="restoreExperiment('${esc(plan.path)}')">
            <span>已归档</span>
            <strong>${esc(plan.name)}</strong>
            <p>${esc(plan.scenario || '—')} · ${esc(scale)}<br>${esc(plan.updated_at || '')}</p>
          </button>
        `;
      }).join('');
      const detail = rows[0];
      const detailScale = detail.error ? detail.error : `${detail.sweeps ? '参数扫描' : '单场景'} · MC ${detail.monte_carlo_samples || 0}`;
      archivedExperimentPlans.innerHTML = `
        <div class="history-timeline">
          <h3>已归档计划</h3>
          <div class="history-grid">${cards}</div>
          <div class="history-detail" style="margin-bottom:0">
            <strong>${esc(detail.name)} · 归档计划</strong>
            <p>场景 ${esc(detail.scenario || '—')}，规模 ${esc(detailScale)}，归档文件 ${esc(detail.path)}，最近更新时间 ${esc(detail.updated_at || '—')}。恢复后会重新回到活动实验列表。</p>
            <div class="toolbar" style="margin-top:10px">
              <button type="button" onclick="restoreExperiment('${esc(detail.path)}')">恢复计划</button>
            </div>
          </div>
        </div>
      `;
    }

    function renderWorkspaceOverview() {
      const scenarios = state.workspace?.scenarios || [];
      const experiments = [...(state.workspace?.experiments || [])].sort((a, b) => Number(b.updated_ts || 0) - Number(a.updated_ts || 0));
      const archivedExperiments = [...(state.workspace?.archived_experiments || [])].sort((a, b) => Number(b.updated_ts || 0) - Number(a.updated_ts || 0));
      const dashboards = [...(state.workspace?.dashboards || [])].sort((a, b) => Number(b.updated_ts || 0) - Number(a.updated_ts || 0));
      const assetSummary = state.workspace?.asset_summary || {};
      const latestPlan = experiments[0] || null;
      const latestResult = dashboards[0] || null;
      const pendingPlans = experiments.filter(plan => !matchingDashboardForPlan(plan));
      const attentionResults = dashboards.filter(item => Number(item.acceptance_rate || 0) < 0.999);
      const latestRate = latestResult ? `${Math.round((latestResult.acceptance_rate || 0) * 1000) / 10}%` : '—';
      const routeStages = standardRouteStageRecords(dashboards);
      const overviewWorkbenchView = ['position', 'route', 'assets'].includes(state.overviewWorkbenchView) ? state.overviewWorkbenchView : 'position';
      state.overviewWorkbenchView = overviewWorkbenchView;
      renderOverviewVisualMaps({
        scenarios,
        experiments,
        archivedExperiments,
        dashboards,
        latestPlan,
        latestResult,
        pendingPlans,
        attentionResults,
      });
      overviewWorkbenchToggle?.querySelectorAll('[data-overview-workbench-view]').forEach(button => {
        button.classList.toggle('active', button.dataset.overviewWorkbenchView === overviewWorkbenchView);
      });
      overviewWorkbenchViews.forEach(node => {
        node.hidden = node.dataset.overviewWorkbenchView !== overviewWorkbenchView;
      });
      if (overviewWorkbenchSummary) {
        overviewWorkbenchSummary.textContent = overviewWorkbenchSummaryText(overviewWorkbenchView);
      }
      if (overviewPositionSignals) {
        const routeReadyCount = routeStages.filter(stage => stage.plan || stage.result).length;
        const recommendedAction = pendingPlans.length
          ? '先补结果'
          : attentionResults.length
            ? '先诊断结果'
            : dashboards.length
              ? '继续扩实验'
              : '先跑标准主线';
        overviewPositionSignals.innerHTML = `
          <div class="summary-card"><span>当前阶段</span><strong>${esc(dashboards.length ? '结果闭环已形成' : '实验工作流优先')}</strong></div>
          <div class="summary-card"><span>主线覆盖</span><strong>${esc(`${routeReadyCount} / ${routeStages.length || 0}`)}</strong></div>
          <div class="summary-card"><span>资产状态</span><strong>${esc(`标准 ${fmt(assetSummary.library_count || 0)} / 演示 ${fmt(assetSummary.showcase_count || 0)}`)}</strong></div>
          <div class="summary-card"><span>下一动作</span><strong>${esc(recommendedAction)}</strong></div>
        `;
      }
      if (overviewPriorityRoute) {
        const focusStage = routeStages[0] || null;
        const gapStage = routeStages.find(stage => !stage.result) || routeStages.find(stage => !stage.plan) || routeStages[1] || null;
        const showcaseStage = routeStages.find(stage => stage.id === 'mission_sequence') || routeStages.find(stage => stage.id === 'actuator_margin') || routeStages[routeStages.length - 1] || null;
        const stageSlots = [
          {slot: '建议先跑', note: '先把最小闭环和标准主线跑通。', stage: focusStage},
          {slot: gapStage && !gapStage.result ? '当前主线缺口' : '当前推进点', note: gapStage && !gapStage.result ? '优先补还没有代表结果的主线环节。' : '当前主线已经具备基础资产，可以继续向前推进。', stage: gapStage},
          {slot: '展示亮点', note: '答辩或演示时优先拿这一环说明平台不是单一曲线展示。', stage: showcaseStage},
        ].filter(item => item.stage);
        const uniqueSlots = [];
        const seenStageIds = new Set();
        for (const item of stageSlots) {
          if (seenStageIds.has(item.stage.id)) continue;
          seenStageIds.add(item.stage.id);
          uniqueSlots.push(item);
        }
        overviewPriorityRoute.innerHTML = `
          <div class="history-detail" style="margin-bottom:0">
            <strong>主线入口建议</strong>
            <p>首页右侧不再堆长说明，而是固定给出当前最适合先跑、先补和先展示的实验入口。</p>
            <div class="overview-entry-grid">
              ${uniqueSlots.map(item => `
                <div class="overview-entry-card">
                  <span>${esc(item.slot)}</span>
                  <strong>${esc(`${item.stage.step} · ${item.stage.label}`)}</strong>
                  <p>${esc(item.stage.summary)}</p>
                  <div class="chips">
                    <span class="chip">${esc(item.stage.status)}</span>
                    <span class="chip">${esc(item.stage.experiment?.label || item.stage.experimentId)}</span>
                  </div>
                  <p>${esc(item.note)}</p>
                  <div class="detail-box">
                    <strong>当前资产</strong>
                    <div>${esc(item.stage.assetSummary)}</div>
                  </div>
                  <div class="detail-box">
                    <strong>现在回答的问题</strong>
                    <div>${esc(item.stage.question)}</div>
                  </div>
                  <div class="toolbar" style="margin-top:auto; margin-bottom:0">
                    <button type="button" onclick="${item.stage.primaryActionCode}">${esc(item.stage.primaryActionLabel)}</button>
                    <button class="secondary" type="button" onclick="previewRoadmapExperiment('${esc(item.stage.experimentId)}')">定位实验</button>
                  </div>
                </div>
              `).join('')}
            </div>
          </div>
        `;
      }
      if (overviewWorkbench) {
        const routeSummary = [
          '闭环基线',
          '控制器 benchmark',
          '统计鲁棒性',
          '环境 / 任务',
          '执行器边界',
        ];
        overviewWorkbench.innerHTML = overviewWorkbenchView === 'route'
          ? `
            <div class="history-detail" style="margin-bottom:0">
              <strong>实验主线总览台</strong>
              <p>首页先把标准实验路径和首开 showcase 路径压成一屏，避免第一次进入平台时面对太多离散入口。</p>
              <div class="flow-sequence" style="margin-top:10px">
                ${routeSummary.map((step, index) => `
                  <div class="flow-node ${index === 0 ? 'accent' : index === routeSummary.length - 1 ? 'ok' : ''}">
                    <small>${esc(`第 ${index + 1} 步`)}</small>
                    <strong>${esc(step)}</strong>
                    <p>${esc(index === 0
                      ? '先确认最小闭环和默认系统工作正常。'
                      : index === 1
                        ? '再用 benchmark 稳定控制器比较口径。'
                        : index === 2
                          ? '再看随机边界和统计稳健性。'
                          : index === 3
                            ? '然后扩到环境扰动与任务模式切换。'
                            : '最后收口到执行器边界和验收判断。')}</p>
                  </div>
                `).join('')}
              </div>
              <div class="detail-grid" style="margin-top:12px">
                <div class="detail-box"><strong>标准主线</strong><div>适合长期研究推进和资产沉淀。</div></div>
                <div class="detail-box"><strong>Showcase 路径</strong><div>适合第一次答辩、汇报或平台演示时快速覆盖闭环、benchmark、环境、mission 和 margin。</div></div>
                <div class="detail-box"><strong>当前入口</strong><div>${esc(latestPlan ? `最近计划 ${latestPlan.name}` : '当前还没有活动计划，可先从实验库或创建实验开始。')}</div></div>
                <div class="detail-box"><strong>下一动作</strong><div>${esc(latestResult ? '可以沿最新结果回到结果层继续读图和回放。' : '先跑一轮标准实验，形成第一份结果资产。')}</div></div>
              </div>
            </div>
          `
          : overviewWorkbenchView === 'assets'
            ? `
              <div class="history-detail" style="margin-bottom:0">
                <strong>当前资产总览台</strong>
                <p>先把工作区资产压成一屏：最近计划、最近结果、待运行计划和待关注结果，让首页更像平台入口而不是纯介绍页。</p>
                <div class="summary-grid" style="margin-top:10px">
                  <div class="summary-card"><span>场景</span><strong>${scenarios.length}</strong></div>
                  <div class="summary-card"><span>活动计划</span><strong>${experiments.length}</strong></div>
                  <div class="summary-card"><span>结果界面</span><strong>${dashboards.length}</strong></div>
                  <div class="summary-card"><span>待关注</span><strong>${attentionResults.length}</strong></div>
                </div>
                <div class="detail-grid" style="margin-top:12px">
                  <div class="detail-box"><strong>最近计划</strong><div>${esc(latestPlan ? `${latestPlan.name} · ${latestPlan.scenario || '—'}` : '当前还没有活动计划。')}</div></div>
                  <div class="detail-box"><strong>最近结果</strong><div>${esc(latestResult ? `${latestResult.name} · 通过率 ${latestRate}` : '当前还没有结果界面。')}</div></div>
                  <div class="detail-box"><strong>待运行计划</strong><div>${esc(pendingPlans.length ? `${pendingPlans.length} 个计划待运行，建议先形成标准结果目录。` : '当前待运行计划已清空。')}</div></div>
                  <div class="detail-box"><strong>待关注结果</strong><div>${esc(attentionResults.length ? `${attentionResults.length} 份结果需要先诊断。` : '当前结果整体比较稳定。')}</div></div>
                </div>
              </div>
            `
            : `
              <div class="history-detail" style="margin-bottom:0">
                <strong>平台定位总览台</strong>
                <p>首页先回答三件事：平台在做什么、实验是怎么闭环的、当前阶段适合拿它解决什么问题。</p>
                <div class="summary-grid" style="margin-top:10px">
                  <div class="summary-card"><span>平台定位</span><strong>姿控仿真实验平台</strong></div>
                  <div class="summary-card"><span>工作流</span><strong>场景 -> 计划 -> 运行 -> 结果</strong></div>
                  <div class="summary-card"><span>当前阶段</span><strong>${esc(dashboards.length ? '结果闭环已形成' : '实验工作流优先')}</strong></div>
                  <div class="summary-card"><span>适合用途</span><strong>研究 / 演示 / 复查</strong></div>
                </div>
                <div class="detail-grid" style="margin-top:12px">
                  <div class="detail-box"><strong>平台在解决什么</strong><div>把姿态仿真、实验组织、结果归档和中文界面展示连成一条稳定链路。</div></div>
                  <div class="detail-box"><strong>为什么不是只做脚本</strong><div>因为场景、变量、验收和结果入口先标准化后，多速率、高保真和数据库扩展才不会把实验资产打散。</div></div>
                  <div class="detail-box"><strong>当前推荐用法</strong><div>先沿标准实验路径形成一组代表资产，再在各步派生自己的计划和结果。</div></div>
                  <div class="detail-box"><strong>当前状态</strong><div>${esc(latestResult ? `当前已经有 ${dashboards.length} 份结果界面，可直接进入结果层继续读图和回放。` : '当前还没有结果界面，建议先从实验库或创建实验跑一轮标准实验。')}</div></div>
                </div>
              </div>
            `;
      }
      overviewCards.innerHTML = `
        <div class="card"><span>场景库</span><strong>${scenarios.length}</strong></div>
        <div class="card"><span>实验计划</span><strong>${experiments.length}</strong></div>
        <div class="card"><span>已归档计划</span><strong>${archivedExperiments.length}</strong></div>
        <div class="card"><span>结果界面</span><strong>${dashboards.length}</strong></div>
        <div class="card"><span>最新通过率</span><strong>${esc(latestRate)}</strong></div>
        <div class="card"><span>待运行计划</span><strong>${pendingPlans.length}</strong></div>
        <div class="card"><span>待关注结果</span><strong>${attentionResults.length}</strong></div>
      `;

      overviewLatestPlan.innerHTML = latestPlan ? `
        <strong>最近实验计划</strong>
        <p>${esc(latestPlan.name)} · 场景 ${esc(latestPlan.scenario || '—')} · ${latestPlan.sweeps ? '参数扫描' : '单场景'} / MC ${esc(latestPlan.monte_carlo_samples || 0)}。最近更新时间 ${esc(latestPlan.updated_at || '—')}。</p>
        <div class="toolbar" style="margin-top:10px">
          <button type="button" onclick="showExperiment('${esc(latestPlan.path)}')">查看计划</button>
          <button class="secondary" type="button" onclick="runPlan('${esc(latestPlan.path)}')">运行计划</button>
          <button class="secondary" type="button" onclick="${matchingDashboardForPlan(latestPlan) ? `showDashboard('${esc(matchingDashboardForPlan(latestPlan).path)}')` : 'void(0)'}" ${matchingDashboardForPlan(latestPlan) ? '' : 'disabled'}>查看结果</button>
        </div>
      ` : `
        <strong>最近实验计划</strong>
        <p>还没有实验计划。可以先从右侧“创建实验”模板开始。</p>
      `;

      overviewLatestResult.innerHTML = latestResult ? `
        <strong>最近实验结果</strong>
        <p>${esc(latestResult.name)} · 场景 ${esc(latestResult.scenario || '—')} · run ${esc(latestResult.run_count)} · 通过率 ${esc(latestRate)}。最近更新时间 ${esc(latestResult.updated_at || '—')}。</p>
        ${latestAlertStripHtml(latestResult)}
        <div class="toolbar" style="margin-top:10px">
          <button type="button" onclick="showDashboard('${esc(latestResult.path)}')">查看结果</button>
          <button class="secondary" type="button" onclick="window.open('${esc(latestResult.url)}', '_blank')">新窗口打开</button>
          <button class="secondary" type="button" onclick="${[...(state.workspace?.experiments || [])].find(plan => matchingDashboardForPlan(plan)?.path === latestResult.path) ? `showExperiment('${esc([...(state.workspace?.experiments || [])].find(plan => matchingDashboardForPlan(plan)?.path === latestResult.path).path)}')` : 'void(0)'}" ${[...(state.workspace?.experiments || [])].find(plan => matchingDashboardForPlan(plan)?.path === latestResult.path) ? '' : 'disabled'}>查看对应计划</button>
        </div>
        ${recentDashboardTrendHtml(latestResult.path)}
      ` : `
        <strong>最近实验结果</strong>
        <p>还没有结果界面。运行一次实验后，这里会自动形成结果入口。</p>
      `;

      const attentionItems = [
        ...attentionResults.slice(0, 2).map(item => `
          <button class="history-item" type="button" onclick="showDashboard('${esc(item.path)}')">
            <span class="history-time">待关注结果</span>
            <div><strong>${esc(item.name)}</strong><p>${esc(item.scenario || '—')} · 通过率 ${Math.round((item.acceptance_rate || 0) * 1000) / 10}%</p></div>
            <span class="chip">查看</span>
          </button>
        `),
        ...pendingPlans.slice(0, 2).map(plan => `
          <button class="history-item" type="button" onclick="showExperiment('${esc(plan.path)}')">
            <span class="history-time">待运行计划</span>
            <div><strong>${esc(plan.name)}</strong><p>${esc(plan.scenario || '—')} · ${plan.sweeps ? '参数扫描' : '单场景'} / MC ${esc(plan.monte_carlo_samples || 0)}</p></div>
            <span class="chip">运行</span>
          </button>
        `),
        ...(archivedExperiments.length ? [`
          <button class="history-item" type="button" onclick="scrollToArchivedPlans()">
            <span class="history-time">已归档计划</span>
            <div><strong>${esc(archivedExperiments[0].name)}</strong><p>当前共有 ${archivedExperiments.length} 个归档计划，可按需恢复到活动列表。</p></div>
            <span class="chip">恢复</span>
          </button>
        `] : []),
      ];
      overviewAttention.innerHTML = attentionItems.length ? `
        <div class="history-timeline">
          <h3>待关注事项</h3>
          <div class="history-items">${attentionItems.join('')}</div>
        </div>
      ` : `
        <div class="callout" style="margin-bottom:0">
          <strong>待关注事项</strong>
          <p>当前没有待关注结果或待运行计划。平台状态比较整洁，可以继续扩展新实验。</p>
        </div>
      `;
    }

    async function load({preserveSelection = true} = {}) {
      setStatus('正在读取工作区...');
      state.workspace = await api('/api/workspace');
      state.selectedExperiments = (state.selectedExperiments || []).filter(path =>
        (state.workspace.experiments || []).some(plan => plan.path === path)
      );
      const newestDashboard = [...(state.workspace.dashboards || [])].sort((a, b) => Number(b.updated_ts || 0) - Number(a.updated_ts || 0))[0] || null;
      if (!state.latestRunDashboard && newestDashboard) {
        state.latestRunDashboard = newestDashboard.path;
      }
      if (!state.latestRunSummary && newestDashboard) {
        state.latestRunSummary = {
          ...newestDashboard,
          experiment_name: newestDashboard.name,
          scenario_name: newestDashboard.scenario,
        };
      }
      experimentFilter.value = state.experimentFilter;
      experimentStatusFilter.value = state.experimentStatusFilter;
      document.getElementById('workspace').textContent = state.workspace.workspace;
      document.getElementById('scenario-count').textContent = state.workspace.scenarios.length;
      document.getElementById('experiment-count').textContent = state.workspace.experiments.length;
      document.getElementById('archived-experiment-count').textContent = (state.workspace.archived_experiments || []).length;
      document.getElementById('dashboard-count').textContent = state.workspace.dashboards.length;
      renderWorkspaceOverview();
      renderExperiments();
      renderExperimentListSwitcher();
      renderExperimentBatchBar();
      renderExperimentPicker();
      renderEditorQuickLoad();
      renderRecentExperimentPlans();
      renderArchivedExperimentPlans();
      renderScenarios();
      renderDashboards();
      renderBuilder();
      renderQuickDemoCards();
      renderQuickDemoStatus();
      renderStandardExperimentRoute();
      renderLibraryProtocolMap();
      renderExperimentLibraryCards();
      renderBuilderViewMode();
      renderBuilderResult();
      syncCreateButtons();
      renderRunStatusPanels();
      if (state.workspace.scenarios.length && (!preserveSelection || !state.currentScenario)) {
        await showScenario(state.workspace.scenarios[0].path, true);
      }
      if (state.workspace.dashboards.length) {
        const preferred = preserveSelection
          ? state.workspace.dashboards.find(item => item.path === state.currentDashboard)?.path
          : null;
        await showDashboard(preferred || newestDashboard?.path || state.workspace.dashboards[0].path, true);
      } else if (!preserveSelection) {
        clearDashboardPreview();
      }
      setStatus('就绪。', 'ok');
      pushActivity('工作区已刷新', `发现 ${state.workspace.scenarios.length} 个场景、${state.workspace.experiments.length} 个实验计划、${state.workspace.dashboards.length} 个结果界面。`, 'workspace');
    }

    function renderBuilder() {
      builderScenario.innerHTML = state.workspace.scenarios.map(item => `<option value="${esc(item.path)}">${esc(item.name)} - ${esc(item.path)}</option>`).join('');
      if (!builderScenario.value && state.workspace.scenarios[0]) builderScenario.value = state.workspace.scenarios[0].path;
      if (!builderAcceptancePreset.value) builderAcceptancePreset.value = 'standard_hold';
      applyAcceptancePreset(builderAcceptancePreset.value, {force: true});
      builderSweepPreset.value = knownSweepPresetFromPath(builderSweepPath.value || builderSweepPreset.value || 'controller.pd_kp');
      if (!builderSweepPath.value && builderSweepPreset.value !== 'custom') {
        builderSweepPath.value = builderSweepPreset.value;
      }
      builderSecondSweepPreset.value = knownSweepPresetFromPath(builderSecondSweepPath.value || builderSecondSweepPreset.value || 'custom');
      if (builderSecondSweepPreset.value === 'custom' && !String(builderSecondSweepPath.value || '').trim()) {
        builderSecondSweepValues.value = builderSecondSweepValues.value || '';
      } else if (!builderSecondSweepPath.value && builderSecondSweepPreset.value !== 'custom') {
        builderSecondSweepPath.value = builderSecondSweepPreset.value;
      }
      renderBuilderTemplateBrowser();
      renderSweepValuePresets();
      renderSecondSweepValuePresets();
      updateBuilderHints();
      renderBuilderStage();
    }

    function parseBuilderValues(text) {
      const raw = String(text || '').trim();
      if (!raw) return [];
      return raw.split(',').map(item => item.trim()).filter(Boolean);
    }

    function sweepPresetConfig(preset) {
      const configs = {
        'custom': {
          path: '',
          values: [],
          label: '手动填写',
          help: '自定义任意可写配置路径，适合高级实验或临时验证。',
          valuePresets: [],
        },
        'controller.pd_kp': {
          path: 'controller.pd_kp',
          values: ['0.15,0.2,0.25', '0.3,0.4,0.5'],
          label: '控制器比例增益',
          help: '扫描比例增益，适合观察收敛速度、超调和稳态误差之间的变化。',
          valuePresets: [
            {label: '保守', values: '0.15,0.2,0.25', help: '低增益，小力矩，适合先看稳定性。'},
            {label: '标准', values: '0.3,0.4,0.5', help: '中等增益，适合快速做第一轮对比。'},
          ],
        },
        'controller.pd_kd': {
          path: 'controller.pd_kd',
          values: ['0.03,0.05,0.07', '0.08,0.1,0.12'],
          label: '控制器微分增益',
          help: '扫描微分增益，适合观察阻尼效果、振荡收敛和控制力矩平滑性。',
          valuePresets: [
            {label: '保守阻尼', values: '0.03,0.05,0.07', help: '更偏平稳，适合先压振荡。'},
            {label: '增强阻尼', values: '0.08,0.1,0.12', help: '更快压制误差，但可能增加控制动作。'},
          ],
        },
        'system.controller': {
          path: 'system.controller',
          values: ['"pd","ladrc"'],
          label: '控制器类型',
          help: '扫描控制器类型，适合在同一场景下做 PD 与 LADRC 的基线 benchmark 对比。',
          valuePresets: [
            {label: 'PD 对 LADRC', values: '"pd","ladrc"', help: '固定场景和任务，只比较控制律差异。'},
          ],
        },
        'system.environment': {
          path: 'system.environment',
          values: ['"zero","orbital"'],
          label: '环境模型',
          help: '扫描环境配置，适合比较理想零扰动与轨道环境扰动条件下的误差、力矩和扰动力矩预算变化。',
          valuePresets: [
            {label: '零扰动对轨道', values: '"zero","orbital"', help: '快速比较不含环境扰动与含轨道扰动条件下的闭环差异。'},
          ],
        },
        'system.disturbance_profile': {
          path: 'system.disturbance_profile',
          values: ['"gravity_gradient_only","residual_magnetic_only","aerodynamic_only","solar_pressure_only","all"'],
          label: '扰动配置模板',
          help: '扫描不同环境扰动模板，适合比较哪一类扰动在当前轨道和构型下最值得优先关注。',
          valuePresets: [
            {label: '四类扰动拆分', values: '"gravity_gradient_only","residual_magnetic_only","aerodynamic_only","solar_pressure_only","all"', help: '逐项比较重力梯度、残余磁矩、气动、太阳压以及全部扰动共同作用时的差异。'},
          ],
        },
        'sensors.gyro.noise_std_rad_s': {
          path: 'sensors.gyro.noise_std_rad_s',
          values: ['0.0005,0.001,0.002', '0.001,0.002,0.004'],
          label: '陀螺噪声强度',
          help: '扫描陀螺测量噪声，适合评估测量质量下降后误差增长和通过率变化。',
          valuePresets: [
            {label: '轻噪声', values: '0.0005,0.001,0.002', help: '适合看从基线到中度退化的过渡。'},
            {label: '中高噪声', values: '0.001,0.002,0.004', help: '适合观察明显测量退化后的闭环边界。'},
          ],
        },
        'time.seed': {
          path: 'time.seed',
          values: ['1,2,3,4', '10,20,30,40'],
          label: '随机种子',
          help: '扫描种子，适合做噪声敏感性、初始化敏感性和可重复性检查。',
          valuePresets: [
            {label: '连续种子', values: '1,2,3,4', help: '适合小规模 Monte Carlo 预演。'},
            {label: '分散种子', values: '10,20,30,40', help: '适合更明显地区分随机序列。'},
          ],
        },
        'actuators.reaction_wheels.max_torque_nm': {
          path: 'actuators.reaction_wheels.max_torque_nm',
          values: ['0.002,0.004,0.006', '0.008,0.01,0.012'],
          label: '轮组最大力矩',
          help: '扫描执行机构能力，适合评估轮组能力对收敛和饱和的影响。',
          valuePresets: [
            {label: '低力矩', values: '0.002,0.004,0.006', help: '适合看受限执行器下的性能。'},
            {label: '高力矩', values: '0.008,0.01,0.012', help: '适合比较更强执行器带来的收敛速度。'},
          ],
        },
        'actuators.reaction_wheels.momentum_gain': {
          path: 'actuators.reaction_wheels.momentum_gain',
          values: ['0.0,0.02,0.05', '0.05,0.1,0.2'],
          label: '轮组动量管理增益',
          help: '扫描动量管理增益，适合研究轮速约束、动量回收和姿态闭环之间的平衡。',
          valuePresets: [
            {label: '温和管理', values: '0.0,0.02,0.05', help: '适合看不管理到轻度管理之间的变化。'},
            {label: '积极管理', values: '0.05,0.1,0.2', help: '适合看更强轮速回拉对控制表现的影响。'},
          ],
        },
      };
      return configs[preset] || configs.custom;
    }

    function acceptancePresetConfig(preset) {
      const configs = {
        standard_hold: {
          label: '标准姿态保持',
          thresholds: {final: 40, rms: 40, torque: 0.2},
          help: '适合一般闭环验证，平衡通过率和区分度。',
        },
        strict_hold: {
          label: '严格闭环验证',
          thresholds: {final: 20, rms: 20, torque: 0.1},
          help: '适合做参数收敛质量对比，对误差和控制动作要求更高。',
        },
        transition_demo: {
          label: '模式切换演示',
          thresholds: {final: 60, rms: 60, torque: 0.08},
          help: '适合 detumble 或模式切换场景，允许更高过渡误差，但限制控制动作过猛。',
        },
        actuator_limited: {
          label: '执行器受限验证',
          thresholds: {final: 60, rms: 60, torque: 0.05},
          help: '适合反作用轮或执行器能力对比，重点看力矩限制下能否达到可接受性能。',
        },
        custom: {
          label: '自定义验收',
          thresholds: null,
          help: '手动填写末端误差、RMS 误差和峰值力矩阈值。',
        },
      };
      return configs[preset] || configs.standard_hold;
    }

    function knownSweepPresetFromPath(path) {
      const text = String(path || '').trim();
      const known = [
        'controller.pd_kp',
        'controller.pd_kd',
        'system.controller',
        'system.environment',
        'system.disturbance_profile',
        'sensors.gyro.noise_std_rad_s',
        'time.seed',
        'actuators.reaction_wheels.max_torque_nm',
        'actuators.reaction_wheels.momentum_gain',
      ];
      return known.includes(text) ? text : 'custom';
    }

    function experimentTemplateConfig(templateId) {
      const configs = {
        pd_tuning: {
          label: 'PD 参数整定',
          nameSuffix: 'pd_tuning',
          sweepPreset: 'controller.pd_kp',
          sweepValues: '0.15,0.2,0.25',
          acceptancePreset: 'strict_hold',
          mission: 'single_mode',
          mode: 'inertial_hold',
          reference: 'body_zero',
          mcSamples: 0,
          mcSeed: '',
          detumbleS: 0.5,
        },
        mc_robustness: {
          label: '随机鲁棒性',
          nameSuffix: 'mc_robustness',
          sweepPreset: 'custom',
          sweepValues: '',
          acceptancePreset: 'standard_hold',
          mission: 'single_mode',
          mode: 'inertial_hold',
          reference: 'body_zero',
          mcSamples: 8,
          mcSeed: 10,
          detumbleS: 0.5,
        },
        sun_transition: {
          label: '太阳指向切换',
          nameSuffix: 'sun_transition',
          sweepPreset: 'controller.pd_kd',
          sweepValues: '0.03,0.05,0.07',
          acceptancePreset: 'transition_demo',
          mission: 'detumble_then_hold',
          mode: 'sun_pointing',
          reference: 'sun',
          mcSamples: 0,
          mcSeed: '',
          detumbleS: 0.6,
        },
        earth_transition: {
          label: '对地指向切换',
          nameSuffix: 'earth_transition',
          sweepPreset: 'controller.pd_kd',
          sweepValues: '0.03,0.05,0.07',
          acceptancePreset: 'transition_demo',
          mission: 'detumble_then_hold',
          mode: 'earth_pointing',
          reference: 'nadir',
          mcSamples: 0,
          mcSeed: '',
          detumbleS: 0.8,
        },
        wheel_capability: {
          label: '执行器能力对比',
          nameSuffix: 'wheel_capability',
          sweepPreset: 'actuators.reaction_wheels.max_torque_nm',
          sweepValues: '0.002,0.004,0.006',
          acceptancePreset: 'actuator_limited',
          mission: 'single_mode',
          mode: 'inertial_hold',
          reference: 'body_zero',
          mcSamples: 0,
          mcSeed: '',
          detumbleS: 0.5,
        },
        controller_benchmark: {
          label: '控制器基准对比',
          nameSuffix: 'controller_benchmark',
          sweepPreset: 'system.controller',
          sweepValues: '"pd","ladrc"',
          acceptancePreset: 'strict_hold',
          mission: 'single_mode',
          mode: 'inertial_hold',
          reference: 'body_zero',
          mcSamples: 0,
          mcSeed: '',
          detumbleS: 0.5,
        },
        environment_sensitivity: {
          label: '环境敏感性',
          nameSuffix: 'environment_sensitivity',
          sweepPreset: 'system.environment',
          sweepValues: '"zero","orbital"',
          acceptancePreset: 'standard_hold',
          mission: 'single_mode',
          mode: 'inertial_hold',
          reference: 'body_zero',
          mcSamples: 0,
          mcSeed: '',
          detumbleS: 0.5,
        },
        disturbance_breakdown: {
          label: '环境扰动分解',
          nameSuffix: 'disturbance_breakdown',
          sweepPreset: 'system.disturbance_profile',
          sweepValues: '"gravity_gradient_only","residual_magnetic_only","aerodynamic_only","solar_pressure_only","all"',
          acceptancePreset: 'standard_hold',
          mission: 'single_mode',
          mode: 'inertial_hold',
          reference: 'body_zero',
          mcSamples: 0,
          mcSeed: '',
          detumbleS: 0.5,
        },
        disturbance_capability_tradeoff: {
          label: '扰动-执行器权衡',
          nameSuffix: 'disturbance_capability_tradeoff',
          sweepPreset: 'system.disturbance_profile',
          sweepValues: '"residual_magnetic_only","aerodynamic_only","all"',
          secondSweepPreset: 'actuators.reaction_wheels.max_torque_nm',
          secondSweepValues: '0.003,0.005,0.007',
          acceptancePreset: 'actuator_limited',
          mission: 'single_mode',
          mode: 'inertial_hold',
          reference: 'body_zero',
          mcSamples: 0,
          mcSeed: '',
          detumbleS: 0.5,
        },
        sensor_sensitivity: {
          label: '测量质量敏感性',
          nameSuffix: 'sensor_sensitivity',
          sweepPreset: 'sensors.gyro.noise_std_rad_s',
          sweepValues: '0.0005,0.001,0.002',
          acceptancePreset: 'standard_hold',
          mission: 'single_mode',
          mode: 'inertial_hold',
          reference: 'body_zero',
          mcSamples: 0,
          mcSeed: '',
          detumbleS: 0.5,
        },
        momentum_management: {
          label: '轮速与动量管理',
          nameSuffix: 'momentum_management',
          sweepPreset: 'actuators.reaction_wheels.momentum_gain',
          sweepValues: '0.0,0.02,0.05',
          acceptancePreset: 'actuator_limited',
          mission: 'single_mode',
          mode: 'inertial_hold',
          reference: 'body_zero',
          mcSamples: 0,
          mcSeed: '',
          detumbleS: 0.5,
        },
        acceptance_gate: {
          label: '严格验收门限',
          nameSuffix: 'acceptance_gate',
          sweepPreset: 'controller.pd_kp',
          sweepValues: '0.12,0.15,0.2,0.25,0.3',
          acceptancePreset: 'strict_hold',
          mission: 'single_mode',
          mode: 'inertial_hold',
          reference: 'body_zero',
          mcSamples: 0,
          mcSeed: '',
          detumbleS: 0.5,
        },
      };
      return configs[templateId] || null;
    }

    function quickDemoConfig(demoId) {
      const configs = {
        quick_pd_showcase: {
          label: '快速闭环演示',
          description: '基于 quick_pd_zero 场景做 PD 参数整定，适合第一次打开平台时直接看结果图和姿态误差动画。',
          scenarioPaths: ['scenarios/quick_pd_zero.json'],
          templateId: 'pd_tuning',
          planName: 'quick_pd_showcase',
          focus: '收敛速度、稳态误差、控制力矩对比',
          question: '在最简单的基线姿态保持场景里，PD 控制器是否已经能形成稳定闭环？',
          metrics: 'final_error_deg / rms_error_deg / peak_torque_nm',
        },
        fault_wheel_showcase: {
          label: '反作用轮故障演示',
          description: '基于 cubesat_rw_fault 场景扫描单轮最大力矩，适合展示失效场景下的执行器能力差异。',
          scenarioPaths: ['scenarios/cubesat_rw_fault.json'],
          templateId: 'wheel_capability',
          planName: 'fault_wheel_showcase',
          focus: '力矩约束、饱和风险、故障场景可视化',
          question: '当执行机构能力下降时，姿态闭环会先在哪个指标上表现出退化？',
          metrics: 'peak_torque_nm / final_error_deg / saturation trend',
        },
        sun_transition_showcase: {
          label: '太阳指向切换演示',
          description: '基于 cubesat_rw_fault 场景执行 detumble -> sun_pointing，适合展示任务模式切换、时间线和三维回放。',
          scenarioPaths: ['scenarios/cubesat_rw_fault.json'],
          templateId: 'sun_transition',
          planName: 'sun_transition_showcase',
          focus: '模式切换、姿态回放、任务时间线联动',
          question: '从消旋进入目标模式时，过渡过程是否平滑、是否满足任务时间线预期？',
          metrics: 'transition peak / final_error_deg / control torque',
        },
        earth_transition_showcase: {
          label: '对地指向演示',
          description: '基于 cubesat_rw_fault 场景执行 detumble -> earth_pointing，适合展示对地任务、nadir 参考和姿态回放。',
          scenarioPaths: ['scenarios/cubesat_rw_fault.json'],
          templateId: 'earth_transition',
          planName: 'earth_transition_showcase',
          focus: '对地任务、nadir 参考、模式时间线与回放联动',
          question: '从消旋进入对地任务后，姿态过渡是否平滑，时间线和回放是否与任务意图一致？',
          metrics: 'transition peak / final_error_deg / control torque',
        },
        controller_benchmark_showcase: {
          label: '控制器基准演示',
          description: '基于 quick_controller_benchmark 场景直接比较 PD 和 LADRC，适合做第一版控制器基准展示。',
          scenarioPaths: ['scenarios/quick_controller_benchmark.json'],
          templateId: 'controller_benchmark',
          planName: 'controller_benchmark_showcase',
          focus: '控制器差异、误差收敛、控制律 benchmark',
          question: '在同一姿态保持任务下，PD 和 LADRC 哪个控制器当前表现更优？',
          metrics: 'controller / final_error_deg / rms_error_deg',
        },
        orbital_environment_showcase: {
          label: '轨道环境演示',
          description: '基于 quick_pd_zero 场景比较 zero 与 orbital 环境，适合展示理想闭环和轨道扰动闭环之间的差异。',
          scenarioPaths: ['scenarios/quick_pd_zero.json'],
          templateId: 'environment_sensitivity',
          planName: 'orbital_environment_showcase',
          focus: '环境扰动、控制余量、扰动力矩预算',
          question: '从理想零扰动切到轨道环境后，哪一类误差或力矩指标最先出现可见变化？',
          metrics: 'environment / final_error_deg / peak_disturbance_torque_nm',
        },
        disturbance_breakdown_showcase: {
          label: '扰动分解演示',
          description: '基于 cubesat_rw_orbital_baseline 场景逐项打开主要环境扰动，适合展示哪类扰动在当前轨道和构型下更主导。',
          scenarioPaths: ['scenarios/cubesat_rw_orbital_baseline.json'],
          templateId: 'disturbance_breakdown',
          planName: 'disturbance_breakdown_showcase',
          focus: '主导扰动项、扰动预算、环境解释',
          question: '在当前轨道基线下，是哪一类环境扰动最先主导姿态控制闭环的外部力矩预算？',
          metrics: 'disturbance profile / peak_disturbance_torque_nm / dominant disturbance',
        },
        sensor_quality_showcase: {
          label: '测量质量演示',
          description: '基于 quick_pd_zero 场景扫描陀螺噪声，适合演示测量质量退化对闭环姿态误差的影响。',
          scenarioPaths: ['scenarios/quick_pd_zero.json'],
          templateId: 'sensor_sensitivity',
          planName: 'sensor_quality_showcase',
          focus: '测量质量、姿态误差、通过率变化',
          question: '当陀螺测量噪声增大时，闭环性能会先在哪个指标上退化？',
          metrics: 'gyro noise / final_error_deg / rms_error_deg',
        },
      };
      return configs[demoId] || null;
    }

    function curatedExperimentConfig(experimentId) {
      const configs = {
        quick_pd_gain_sweep: {
          label: '控制器增益扫描',
          description: '基于 quick_pd_zero 的标准参数整定实验，用来比较比例增益变化对收敛速度、末端误差和峰值力矩的影响。',
          scenarioPaths: ['scenarios/quick_pd_zero.json'],
          planPaths: ['scenarios/quick_pd_gain_sweep.json'],
          templateId: 'pd_tuning',
          planName: 'quick_pd_gain_sweep',
          theme: '控制器整定',
          variable: 'controller.pd_kp',
          metrics: 'final_error_deg / rms_error_deg / peak_torque_nm',
          question: '比例增益变大后，收敛速度提升和控制动作增大之间的平衡点在哪里？',
          whenToUse: '适合做第一轮控制器整定，也适合给验收门限找合理起点。',
          outputs: 'summary_metrics / best run / 姿态误差与力矩曲线',
        },
        quick_pd_damping_sweep: {
          label: '阻尼整定实验',
          description: '基于 quick_pd_zero 的微分增益扫描实验，用来比较阻尼增强对振荡收敛、误差平滑性和控制动作的影响。',
          scenarioPaths: ['scenarios/quick_pd_zero.json'],
          planPaths: ['scenarios/quick_pd_damping_sweep.json'],
          templateId: 'pd_tuning',
          planName: 'quick_pd_damping_sweep',
          theme: '控制器整定',
          variable: 'controller.pd_kd',
          metrics: 'rms_error_deg / final_error_deg / peak_torque_nm',
          question: '阻尼增大后，振荡抑制和响应变慢之间的分界点在哪里？',
          whenToUse: '适合在比例增益初步确定后，进一步收紧振荡与平滑性。',
          outputs: 'summary_metrics / run 排行 / 误差与力矩曲线',
        },
        quick_pd_seed_mc: {
          label: '随机鲁棒性实验',
          description: '基于 quick_pd_zero 的轻量 Monte Carlo 实验，用连续 seed 观察噪声和初值对姿态误差与通过率的影响。',
          scenarioPaths: ['scenarios/quick_pd_zero.json'],
          planPaths: ['scenarios/quick_pd_seed_mc.json'],
          templateId: 'mc_robustness',
          planName: 'quick_pd_seed_mc',
          theme: '鲁棒性',
          variable: 'Monte Carlo seeds',
          metrics: 'acceptance_rate / final_error_deg / rms_error_deg',
          question: '控制器在随机扰动、噪声和不同初值下是否仍然稳定，最差工况会差到什么程度？',
          whenToUse: '适合从“能跑”走到“稳不稳”，看通过率和最差 run。',
          outputs: 'acceptance rate / best-worst gap / run 分布',
        },
        quick_controller_benchmark_compare: {
          label: '控制器基准实验',
          description: '基于 quick_controller_benchmark 的控制器对比实验，用统一场景直接比较 PD 与 LADRC 的闭环表现。',
          scenarioPaths: ['scenarios/quick_controller_benchmark.json'],
          planPaths: ['scenarios/quick_controller_benchmark_compare.json'],
          templateId: 'controller_benchmark',
          planName: 'quick_controller_benchmark_compare',
          theme: '控制器 benchmark',
          variable: 'system.controller',
          metrics: 'final_error_deg / rms_error_deg / controller',
          question: '在同一基线姿态保持任务里，当前是 PD 还是 LADRC 更适合做平台默认基线？',
          whenToUse: '适合建立第一版控制器 benchmark，给后续控制器扩展留出统一比较入口。',
          outputs: 'best controller / run summary / 误差对比',
        },
        quick_sensor_noise_sensitivity: {
          label: '测量质量敏感性实验',
          description: '基于 quick_pd_zero 的陀螺噪声扫描实验，用来观察测量质量下降对闭环误差、通过率和最差工况的影响。',
          scenarioPaths: ['scenarios/quick_pd_zero.json'],
          planPaths: ['scenarios/quick_sensor_noise_sensitivity.json'],
          templateId: 'sensor_sensitivity',
          planName: 'quick_sensor_noise_sensitivity',
          theme: '测量敏感性',
          variable: 'sensors.gyro.noise_std_rad_s',
          metrics: 'final_error_deg / rms_error_deg / acceptance_rate',
          question: '测量质量下降后，姿态闭环会先失去哪一部分性能余量？',
          whenToUse: '适合把“随机鲁棒性”进一步收敛为“测量质量敏感性”评估。',
          outputs: 'noise sweep / acceptance summary / error ranking',
        },
        quick_environment_compare: {
          label: '环境敏感性实验',
          description: '基于 quick_pd_zero 的环境配置对比实验，用同一控制器直接比较理想零扰动和轨道环境扰动条件下的闭环差异。',
          scenarioPaths: ['scenarios/quick_pd_zero.json'],
          planPaths: ['scenarios/quick_environment_compare.json'],
          templateId: 'environment_sensitivity',
          planName: 'quick_environment_compare',
          theme: '环境敏感性',
          variable: 'system.environment',
          metrics: 'final_error_deg / rms_error_deg / peak_disturbance_torque_nm',
          question: '从理想 zero 环境切到 orbital 环境后，姿态误差、控制力矩和扰动力矩预算会发生怎样的变化？',
          whenToUse: '适合把平台从纯闭环演示推进到“环境扰动是否重要”的第一轮工程判断。',
          outputs: 'environment sweep / disturbance budget / acceptance summary',
        },
        cubesat_rw_disturbance_breakdown: {
          label: '环境扰动分解实验',
          description: '基于 cubesat_rw_orbital_baseline 的扰动模板扫描实验，逐项比较重力梯度、残余磁矩、气动、太阳压及其共同作用。',
          scenarioPaths: ['scenarios/cubesat_rw_orbital_baseline.json'],
          planPaths: ['scenarios/cubesat_rw_disturbance_breakdown.json'],
          templateId: 'disturbance_breakdown',
          planName: 'cubesat_rw_disturbance_breakdown',
          theme: '扰动分解',
          variable: 'system.disturbance_profile',
          metrics: 'peak_disturbance_torque_nm / dominant disturbance / final_error_deg',
          question: '当前轨道与构型下，哪一类环境扰动最主导姿态误差和扰动力矩预算？',
          whenToUse: '适合把“环境会不会影响结果”继续推进到“具体是哪类扰动最值得优先建模”。',
          outputs: 'disturbance breakdown / dominant disturbance / budget curves',
        },
        cubesat_rw_disturbance_capability_tradeoff: {
          label: '扰动-执行器权衡实验',
          description: '基于 cubesat_rw_orbital_baseline 的双变量实验，同时扫描扰动模板和轮组最大力矩，比较外部环境与执行器能力的耦合边界。',
          scenarioPaths: ['scenarios/cubesat_rw_orbital_baseline.json'],
          planPaths: ['scenarios/cubesat_rw_disturbance_capability_tradeoff.json'],
          templateId: 'wheel_capability',
          planName: 'cubesat_rw_disturbance_capability_tradeoff',
          theme: '执行器边界',
          variable: 'system.disturbance_profile × actuators.reaction_wheels.max_torque_nm',
          metrics: 'final_error_deg / peak_torque_nm / dominant disturbance',
          question: '当主导扰动增强而执行器能力受限时，系统先受环境约束还是先受轮组能力约束？',
          whenToUse: '适合在完成扰动分解后继续评估执行器能力是否足够覆盖主要外部环境。 ',
          outputs: '二维权衡表 / dominant disturbance / acceptance summary',
        },
        cubesat_rw_fault_seed_mc: {
          label: '故障场景 Monte Carlo',
          description: '基于 cubesat_rw_fault 的随机鲁棒性实验，用连续 seed 观察单轮失效后在轨环境和测量噪声下的稳定性。',
          scenarioPaths: ['scenarios/cubesat_rw_fault.json'],
          planPaths: ['scenarios/cubesat_rw_fault_seed_mc.json'],
          templateId: 'mc_robustness',
          planName: 'cubesat_rw_fault_seed_mc',
          theme: '鲁棒性',
          variable: 'Monte Carlo seeds',
          metrics: 'acceptance_rate / final_error_deg / best-worst gap',
          question: '在单轮失效和轨道环境共同作用下，最差 run 会退化到什么程度？',
          whenToUse: '适合从基线闭环走向故障鲁棒性评估，回答“极端工况稳不稳”。',
          outputs: 'acceptance rate / best-worst gap / dashboard 分布',
        },
        cubesat_rw_sun_transition_curated: {
          label: '太阳指向切换实验',
          description: '基于 cubesat_rw_fault 的 detumble -> sun_pointing 任务流程，适合展示任务模式切换、姿态回放和 mode timeline 联动。',
          scenarioPaths: ['scenarios/cubesat_rw_fault.json'],
          planPaths: ['scenarios/cubesat_rw_sun_transition_curated.json'],
          templateId: 'sun_transition',
          planName: 'cubesat_rw_sun_transition_curated',
          theme: '任务模式切换',
          variable: 'controller.pd_kd + mission transition',
          metrics: 'transition peak / final_error_deg / control torque',
          question: '任务模式切换时，误差峰值、力矩波动和模式时间线是否一致且可解释？',
          whenToUse: '适合演示任务流程、模式切换和结果回放，不只看单点指标。',
          outputs: 'mode timeline / runtime schedule / 回放与 dashboard',
        },
        cubesat_rw_earth_transition_curated: {
          label: '对地指向任务实验',
          description: '基于 cubesat_rw_fault 的 detumble -> earth_pointing 任务流程，适合展示对地任务、nadir 参考、姿态回放和 mode timeline 联动。',
          scenarioPaths: ['scenarios/cubesat_rw_fault.json'],
          planPaths: ['scenarios/cubesat_rw_earth_transition_curated.json'],
          templateId: 'earth_transition',
          planName: 'cubesat_rw_earth_transition_curated',
          theme: '任务模式切换',
          variable: 'controller.pd_kd + earth_pointing mission',
          metrics: 'transition peak / final_error_deg / control torque',
          question: '进入对地指向任务后，姿态误差峰值、力矩波动和 nadir 参考时间线是否仍然一致且可解释？',
          whenToUse: '适合把任务实验从太阳指向扩展到更工程化的对地任务场景，验证 mission 与参考定义是否清晰。',
          outputs: 'mode timeline / runtime schedule / 对地任务回放与 dashboard',
        },
        cubesat_rw_earth_transition_wheel_capability: {
          label: '对地任务执行器边界实验',
          description: '基于 cubesat_rw_fault 的 detumble -> earth_pointing 任务流程，扫描轮组最大力矩，比较对地任务在执行器受限时的误差峰值与控制余量。',
          scenarioPaths: ['scenarios/cubesat_rw_fault.json'],
          planPaths: ['scenarios/cubesat_rw_earth_transition_wheel_capability.json'],
          templateId: 'wheel_capability',
          planName: 'cubesat_rw_earth_transition_wheel_capability',
          theme: '执行器边界',
          variable: 'actuators.reaction_wheels.max_torque_nm + earth_pointing mission',
          metrics: 'transition peak / peak_torque_nm / final_error_deg',
          question: '当执行器能力下降时，对地指向任务会先在误差峰值、力矩边界还是模式过渡平滑性上暴露瓶颈？',
          whenToUse: '适合把任务实验和执行器边界实验接到一起，回答“任务可行”与“执行器够不够”是不是同一件事。',
          outputs: 'earth-pointing transition / torque boundary / replay and dashboard summary',
        },
        cubesat_rw_fault_gain_tradeoff: {
          label: '故障场景增益权衡',
          description: '基于 cubesat_rw_fault 的比例增益扫描实验，用来观察故障后更激进控制与控制余量、误差改善之间的平衡。',
          scenarioPaths: ['scenarios/cubesat_rw_fault.json'],
          planPaths: ['scenarios/cubesat_rw_fault_gain_tradeoff.json'],
          templateId: 'pd_tuning',
          planName: 'cubesat_rw_fault_gain_tradeoff',
          theme: '控制器整定',
          variable: 'controller.pd_kp',
          metrics: 'final_error_deg / peak_torque_nm / acceptance_rate',
          question: '故障后继续提高增益，是在改善误差，还是更快逼近控制边界？',
          whenToUse: '适合做失效后参数重整定，比较保守与激进控制的代价。',
          outputs: 'best run / peak torque / acceptance summary',
        },
        cubesat_rw_wheel_capability: {
          label: '执行器能力边界实验',
          description: '基于 cubesat_rw_fault 的轮组能力对比实验，用不同最大力矩观察收敛速度、饱和风险和控制余量变化。',
          scenarioPaths: ['scenarios/cubesat_rw_fault.json'],
          planPaths: ['scenarios/cubesat_rw_wheel_capability.json'],
          templateId: 'wheel_capability',
          planName: 'cubesat_rw_wheel_capability',
          theme: '执行器边界',
          variable: 'actuators.reaction_wheels.max_torque_nm',
          metrics: 'peak_torque_nm / final_error_deg / saturation trend',
          question: '执行机构能力受限时，是误差先恶化，还是饱和和轮速先成为瓶颈？',
          whenToUse: '适合做工程边界判断，回答“这个执行器能力够不够”。',
          outputs: '峰值力矩 / 饱和统计 / 轮组相关趋势',
        },
        cubesat_rw_momentum_management_sweep: {
          label: '轮速与动量管理实验',
          description: '基于 cubesat_rw_momentum_management 的动量管理增益扫描实验，用来比较轮速回拉策略对姿态保持与执行器余量的影响。',
          scenarioPaths: ['scenarios/cubesat_rw_momentum_management.json'],
          planPaths: ['scenarios/cubesat_rw_momentum_management_sweep.json'],
          templateId: 'momentum_management',
          planName: 'cubesat_rw_momentum_management_sweep',
          theme: '轮速管理',
          variable: 'actuators.reaction_wheels.momentum_gain',
          metrics: 'momentum gain / final_error_deg / actuator margin',
          question: '更积极的轮速管理是在改善执行器余量，还是会开始干扰姿态保持性能？',
          whenToUse: '适合从“执行器能力够不够”继续走向“轮速怎么管更合适”。',
          outputs: 'gain sweep / result summary / momentum-management comparison',
        },
        quick_controller_benchmark_orbital_compare: {
          label: '轨道环境控制器基准',
          description: '基于 quick_controller_benchmark_orbital 的控制器比较实验，用统一轻量轨道环境比较 PD 与 LADRC 是否仍保持同样优劣顺序。',
          scenarioPaths: ['scenarios/quick_controller_benchmark_orbital.json'],
          planPaths: ['scenarios/quick_controller_benchmark_orbital_compare.json'],
          templateId: 'controller_benchmark',
          planName: 'quick_controller_benchmark_orbital_compare',
          theme: '控制器 benchmark',
          variable: 'system.controller',
          metrics: 'final_error_deg / peak_torque_nm / acceptance_rate',
          question: '放到轻量轨道环境后，控制器优劣顺序是否依旧稳定，还是会出现新的控制代价差异？',
          whenToUse: '适合把控制器 benchmark 从理想环境推进到更真实的轻量在轨背景。',
          outputs: 'benchmark table / torque comparison / acceptance summary',
        },
        quick_pd_acceptance_gate: {
          label: '严格验收门限实验',
          description: '基于 quick_pd_zero 的比例增益扫描实验，但采用严格验收规则，用来识别哪些参数点只是勉强可用，哪些参数点真正稳健。',
          scenarioPaths: ['scenarios/quick_pd_zero.json'],
          planPaths: ['scenarios/quick_pd_acceptance_gate.json'],
          templateId: 'acceptance_gate',
          planName: 'quick_pd_acceptance_gate',
          theme: '验收门限',
          variable: 'controller.pd_kp + strict acceptance',
          metrics: 'acceptance_rate / final_error_deg / rms_error_deg',
          question: '同一组控制参数在更严格验收下，哪些 run 还能通过，平台默认门限是否过松？',
          whenToUse: '适合在完成基础整定后回看验收标准是否足够区分优劣，也是平台报告口径收紧前的重要一步。',
          outputs: 'strict gate summary / accepted-failed boundary / best-worst comparison',
        },
      };
      return configs[experimentId] || null;
    }

    function experimentLibraryIds() {
      return [
        'quick_pd_gain_sweep',
        'quick_pd_damping_sweep',
        'quick_pd_seed_mc',
        'quick_controller_benchmark_compare',
        'quick_controller_benchmark_orbital_compare',
        'quick_environment_compare',
        'quick_pd_acceptance_gate',
        'cubesat_rw_disturbance_breakdown',
        'cubesat_rw_disturbance_capability_tradeoff',
        'quick_sensor_noise_sensitivity',
        'cubesat_rw_fault_seed_mc',
        'cubesat_rw_sun_transition_curated',
        'cubesat_rw_earth_transition_curated',
        'cubesat_rw_earth_transition_wheel_capability',
        'cubesat_rw_fault_gain_tradeoff',
        'cubesat_rw_wheel_capability',
        'cubesat_rw_momentum_management_sweep',
      ];
    }

    function experimentLibraryCategoryId(config) {
      const theme = String(config?.theme || '');
      if (theme === '控制器整定') return 'tuning';
      if (theme === '控制器 benchmark') return 'benchmark';
      if (theme === '鲁棒性') return 'robustness';
      if (theme === '环境敏感性' || theme === '扰动分解') return 'environment';
      if (theme === '测量敏感性') return 'sensing';
      if (theme === '任务模式切换') return 'mission';
      if (theme === '执行器边界' || theme === '轮速管理') return 'actuator';
      if (theme === '验收门限') return 'acceptance';
      return 'all';
    }

    function experimentLibraryCategoryMeta(categoryId) {
      const mapping = {
        all: {
          label: '全部实验',
          description: '浏览当前平台已经整理好的全部成熟实验模板，适合先建立整体实验地图。',
          reference: '整体实验主线',
        },
        tuning: {
          label: '控制器整定',
          description: '围绕 PD 等控制参数扫描，优先建立闭环稳定性、误差和控制动作之间的基线权衡。',
          reference: '控制器基线与验收门限',
        },
        benchmark: {
          label: '控制器 benchmark',
          description: '在统一场景和统一任务下比较不同控制律，为后续平台默认基线提供证据。',
          reference: '统一控制器比较入口',
        },
        robustness: {
          label: '随机鲁棒性',
          description: '围绕 Monte Carlo、seed、噪声和初值，比较最差工况、通过率和结果离散性。',
          reference: '实验统计与趋势验证',
        },
        environment: {
          label: '环境与扰动',
          description: '把环境是否重要、哪类扰动最主导这些问题拆开验证，对应成熟平台中的 environment setup 视角。',
          reference: 'Tudat 风格环境建模入口',
        },
        sensing: {
          label: '测量与估计',
          description: '围绕测量噪声和感知质量，观察姿态闭环余量如何退化。',
          reference: '传感器与估计链敏感性',
        },
        mission: {
          label: '任务模式切换',
          description: '围绕 detumble、保持、对日、对地等任务流程，验证模式切换和时间线一致性。',
          reference: 'GMAT 风格任务序列入口',
        },
        actuator: {
          label: '执行器边界',
          description: '围绕轮组力矩上限、轮速管理和能力边界，比较执行器余量与姿态性能的耦合。',
          reference: 'Basilisk 风格执行机构边界',
        },
        acceptance: {
          label: '验收与评估',
          description: '围绕更严格的通过标准和结果判读口径，判断当前实验是否真的足够稳健，而不只是能跑通。',
          reference: '实验验收规则与评估口径',
        },
      };
      return mapping[categoryId] || mapping.all;
    }

    function builderCategoryMeta(categoryId) {
      const meta = experimentLibraryCategoryMeta(categoryId);
      const mapping = {
        all: '先建立整体实验地图，再决定这轮是做整定、鲁棒性、环境、任务还是执行器边界。',
        tuning: '先把控制器参数调到合理区间，再继续扩展 Monte Carlo 或环境扰动。',
        benchmark: '适合用统一场景比较不同控制器，给平台默认基线提供证据。',
        robustness: '适合把单次漂亮结果升级成统计结论，重点看通过率和最差工况。',
        environment: '适合先回答环境是否重要，再继续拆到具体扰动项和环境-执行器权衡。',
        mission: '适合把实验从稳态保持推进到 detumble、模式切换和时间线展示。',
        actuator: '适合判断轮组能力是否够用，以及轮速管理和外部扰动之间的耦合边界。',
        sensing: '适合判断感知链退化会先损失哪部分姿态性能余量。',
        acceptance: '适合回看验收规则是否过松或过严，让实验报告中的“通过”更有含金量。',
      };
      return {
        label: meta.label,
        description: mapping[categoryId] || meta.description,
      };
    }

    function builderTemplateCategoryId(templateId) {
      const mapping = {
        pd_tuning: 'tuning',
        controller_benchmark: 'benchmark',
        mc_robustness: 'robustness',
        environment_sensitivity: 'environment',
        disturbance_breakdown: 'environment',
        sun_transition: 'mission',
        earth_transition: 'mission',
        wheel_capability: 'actuator',
        disturbance_capability_tradeoff: 'actuator',
        momentum_management: 'actuator',
        sensor_sensitivity: 'sensing',
        acceptance_gate: 'acceptance',
      };
      return mapping[templateId] || 'all';
    }

    function experimentProtocolConfig(protocolId) {
      const configs = {
        baseline_closure: {
          label: '闭环基线协议',
          summary: '固定单模式保持场景，只改控制参数，先建立可信的姿态闭环基线。',
          objective: '先证明控制器在最小基线里能稳定收敛，再决定后续的 benchmark、鲁棒性和环境扩展。',
          prerequisites: '需要一个单模式保持场景，推荐 quick_pd_zero，并先固定参考目标与环境设置。',
          steps: ['比例增益粗扫找收敛区间', '微分阻尼细扫收紧振荡', '用严格验收回看边界参数点'],
          outputs: 'gain sweep、damping sweep、最佳参数和验收边界',
          reportFigures: '参数扫描代表图、姿态误差对比、accepted-failed boundary',
          decision: '得到一组可作为后续实验默认值的控制参数，而不是只凭单次运行感觉选参数。',
          nextProtocolId: 'robustness_screen',
          categories: ['tuning'],
          matureReference: '成熟平台里通常会先固定 propagation / mission 设置，再只改控制参数建立基线。',
          representativeExperimentIds: ['quick_pd_gain_sweep', 'quick_pd_damping_sweep', 'quick_pd_acceptance_gate'],
        },
        controller_trade: {
          label: '控制器基准协议',
          summary: '在同一任务、同一环境和同一初值口径下，只比较控制器差异。',
          objective: '给平台选出一个当前默认控制器基线，并为后续控制律扩展留下统一比较入口。',
          prerequisites: '先有一组已整定的基线参数和稳定场景，避免把控制器差异和场景差异混在一起。',
          steps: ['固定同一场景与参考', '扫描 controller 类型', '比较误差、力矩与通过率'],
          outputs: 'benchmark 表、代表 run、控制器差异说明',
          reportFigures: '控制器 benchmark 总表、误差-力矩对比、代表控制器动态过程',
          decision: '回答“当前默认基线该选谁”，再把更优控制器推向环境、任务和执行器场景。',
          nextProtocolId: 'environment_budget',
          categories: ['benchmark'],
          matureReference: '对应成熟平台中统一 resource / mission 下的控制律 A/B benchmark 思路。',
          representativeExperimentIds: ['quick_controller_benchmark_compare', 'quick_controller_benchmark_orbital_compare'],
        },
        robustness_screen: {
          label: '鲁棒性筛查协议',
          summary: '把单次漂亮结果升级成统计结论，重点看通过率、最差 run 和结果离散性。',
          objective: '验证当前参数并非只在一个 seed 上好看，而是在随机扰动、初值和噪声下仍然稳定。',
          prerequisites: '应先完成一轮基线整定或控制器 benchmark，得到可复用的默认参数。',
          steps: ['固定基线参数与场景', '运行 Monte Carlo 或 seed 扫描', '优先复查最差 run 与失败原因'],
          outputs: 'acceptance rate、best-worst gap、最差 run 诊断',
          reportFigures: '通过率摘要、best-worst gap 图、最差 run 诊断图',
          decision: '回答“这组参数到底稳不稳”，再决定是否回到整定、感知或故障场景继续收紧。',
          nextProtocolId: 'sensing_chain',
          categories: ['robustness'],
          matureReference: '对应成熟实验平台中用批量统计代替单次截图结论的验证习惯。',
          representativeExperimentIds: ['quick_pd_seed_mc', 'cubesat_rw_fault_seed_mc'],
        },
        environment_budget: {
          label: '环境建模协议',
          summary: '先判断环境是否重要，再拆到具体扰动项和外部力矩预算。',
          objective: '把“轨道环境会不会影响结果”推进到“到底是哪类扰动最主导当前闭环”。',
          prerequisites: '需要一个已能稳定运行的基线控制器，并保证轨道环境输入与参考目标定义清楚。',
          steps: ['先比较 zero 与 orbital', '再逐项拆分扰动模板', '最后把环境与执行器余量连到一起'],
          outputs: 'environment sweep、disturbance breakdown、主导扰动结论',
          reportFigures: '环境对比曲线、主导扰动预算图、环境到执行器链路图',
          decision: '得到后续高保真环境建模最值得优先投入的方向，而不是盲目堆外场模型。',
          nextProtocolId: 'actuator_margin',
          categories: ['environment'],
          matureReference: '对应 Tudat 风格的 environment setup / output 分层，再配合预算分析判读结果。',
          representativeExperimentIds: ['quick_environment_compare', 'cubesat_rw_disturbance_breakdown', 'cubesat_rw_disturbance_capability_tradeoff'],
        },
        mission_sequence: {
          label: '任务序列协议',
          summary: '把稳态保持扩成 detumble -> pointing 的任务流程，验证模式、参考和时间线一致性。',
          objective: '让实验不只停留在稳态指标，而是能回答任务切换段是否平滑、可解释、可回放。',
          prerequisites: '需要 mission 模板、目标模式和参考定义都清楚，最好先有可用的单模式保持基线。',
          steps: ['先定义 detumble 与目标模式', '检查 mode timeline 与参考目标', '在回放中复查切换峰值与时序'],
          outputs: 'mode timeline、runtime schedule、姿态回放与切换诊断',
          reportFigures: '模式时间线图、切换段误差峰值图、姿态回放关键帧',
          decision: '回答任务流程是否合理，再把同一任务推向对地、执行器边界或更复杂 mission。',
          nextProtocolId: 'actuator_margin',
          categories: ['mission'],
          matureReference: '对应 GMAT mission sequence 与 Basilisk runtime 的任务流组织方式。',
          representativeExperimentIds: ['cubesat_rw_sun_transition_curated', 'cubesat_rw_earth_transition_curated'],
        },
        actuator_margin: {
          label: '执行器裕度协议',
          summary: '在任务与环境前提已明确后，验证轮组能力、故障裕度和动量管理是否足够。',
          objective: '回答“任务可行”和“执行器够不够”是不是同一件事，并找到真正的工程边界。',
          prerequisites: '最好先完成环境或任务协议，明确外部扰动和任务模式，再评估轮组能力。',
          steps: ['扫描轮组最大力矩', '观察饱和、轮速和误差峰值', '必要时继续加入动量管理或故障增益权衡'],
          outputs: 'torque boundary、saturation trend、momentum-management comparison',
          reportFigures: '力矩边界图、饱和/轮速趋势图、动量管理对照图',
          decision: '识别执行器瓶颈是在力矩、轮速还是任务过渡阶段，再决定是否提升模型保真度。',
          nextProtocolId: 'acceptance_gate',
          categories: ['actuator'],
          matureReference: '对应 Basilisk 一类平台里围绕 state effector 与 actuator margin 展开的工程验证。',
          representativeExperimentIds: ['cubesat_rw_wheel_capability', 'cubesat_rw_earth_transition_wheel_capability', 'cubesat_rw_momentum_management_sweep'],
        },
        sensing_chain: {
          label: '感知链协议',
          summary: '把噪声与测量质量从总鲁棒性里拆出来，单独评估感知链退化对闭环余量的影响。',
          objective: '回答测量质量下降时，系统先在哪类指标上丢失性能余量，而不是只看最终是否还能跑完。',
          prerequisites: '适合放在已有稳定闭环和基础 Monte Carlo 之后，用来单独追踪感知链的影响。',
          steps: ['固定控制器与任务', '扫描传感器噪声或偏置', '比较通过率、最差 run 和误差曲线'],
          outputs: 'noise sweep、acceptance summary、误差敏感性说明',
          reportFigures: '噪声敏感性曲线、通过率摘要图、最差噪声档位动态过程',
          decision: '明确后续该优先补传感器模型、估计器还是回到控制器整定。',
          nextProtocolId: 'acceptance_gate',
          categories: ['sensing'],
          matureReference: '对应成熟平台中单独隔离 sensor / estimation 影响来源的验证思路。',
          representativeExperimentIds: ['quick_sensor_noise_sensitivity'],
        },
        acceptance_gate: {
          label: '验收口径协议',
          summary: '用明确门限回答“能跑”和“可验收”的差别，形成稳定结果口径。',
          objective: '让平台报告里的“通过”真正有含义，而不是把边界工况和稳健方案混在一起。',
          prerequisites: '应先有一轮基线整定或任务实验结果，再回看当前阈值是否过松或过严。',
          steps: ['明确 final / RMS / torque 门限', '重跑关键参数点或关键 run', '记录 accepted / failed 边界'],
          outputs: 'strict gate summary、accepted-failed boundary、代表 run 解释',
          reportFigures: '严格验收边界图、accepted-failed summary、代表 run 解释图',
          decision: '形成后续 dashboard、实验报告和批量筛选都能复用的统一验收规则。',
          nextProtocolId: 'robustness_screen',
          categories: ['acceptance'],
          matureReference: '对应成熟实验平台中 assessment criteria 与 report contract 的稳定约束。',
          representativeExperimentIds: ['quick_pd_acceptance_gate'],
        },
      };
      return configs[protocolId] || null;
    }

    function experimentProtocolIds(categoryId = 'all') {
      const ids = [
        'baseline_closure',
        'controller_trade',
        'robustness_screen',
        'environment_budget',
        'mission_sequence',
        'actuator_margin',
        'sensing_chain',
        'acceptance_gate',
      ];
      if (categoryId === 'all') return ids;
      return ids.filter(id => (experimentProtocolConfig(id)?.categories || []).includes(categoryId));
    }

    function protocolIdForTemplate(templateId) {
      const mapping = {
        pd_tuning: 'baseline_closure',
        controller_benchmark: 'controller_trade',
        mc_robustness: 'robustness_screen',
        environment_sensitivity: 'environment_budget',
        disturbance_breakdown: 'environment_budget',
        sun_transition: 'mission_sequence',
        earth_transition: 'mission_sequence',
        wheel_capability: 'actuator_margin',
        disturbance_capability_tradeoff: 'actuator_margin',
        momentum_management: 'actuator_margin',
        sensor_sensitivity: 'sensing_chain',
        acceptance_gate: 'acceptance_gate',
      };
      return mapping[templateId] || '';
    }

    function protocolIdForExperiment(experimentId) {
      const config = curatedExperimentConfig(experimentId);
      return protocolIdForTemplate(config?.templateId || '');
    }

    function protocolExperimentIds(protocolId, categoryId = 'all') {
      return experimentLibraryIds().filter(id => {
        if (protocolIdForExperiment(id) !== protocolId) return false;
        if (categoryId === 'all') return true;
        return experimentLibraryCategoryId(curatedExperimentConfig(id)) === categoryId;
      });
    }

    function protocolPrimaryExperimentId(protocolId, categoryId = 'all') {
      return protocolExperimentIds(protocolId, categoryId)[0] || '';
    }

    function protocolPrimaryExperimentLabel(protocolId) {
      const firstId = protocolPrimaryExperimentId(protocolId, 'all');
      const config = curatedExperimentConfig(firstId);
      return config?.label || firstId || '待补充';
    }

    function protocolStepsText(protocol) {
      if (!protocol?.steps?.length) return '当前协议会把入口实验、推荐步骤和结果产物组织在一起。';
      return protocol.steps.map((step, index) => `${index + 1}. ${step}`).join(' -> ');
    }

    function protocolRouteMeta(protocolId) {
      const stage = standardExperimentRouteConfig().find(item => item.protocolId === protocolId) || null;
      if (stage) {
        return {
          slot: stage.step,
          routeLabel: stage.label,
          kind: 'mainline',
        };
      }
      const branch = supportingBranchRouteConfig().find(item => item.anchorProtocolId === protocolId) || null;
      if (branch) {
        const anchorStage = standardExperimentRouteConfig().find(item => item.protocolId === branch.anchorProtocolId) || null;
        return {
          slot: anchorStage ? `${anchorStage.step} 支线` : '关键支线',
          routeLabel: branch.label,
          kind: 'branch',
        };
      }
      return {
        slot: '补充协议',
        routeLabel: experimentProtocolConfig(protocolId)?.label || protocolId || '协议',
        kind: 'supplement',
      };
    }

    function protocolWorkspaceMapRecords(options = {}) {
      const dashboards = options.dashboards || state.workspace?.dashboards || [];
      const plans = options.plans || state.workspace?.experiments || [];
      return experimentProtocolIds('all').map(protocolId => {
        const protocol = experimentProtocolConfig(protocolId);
        const routeMeta = protocolRouteMeta(protocolId);
        const experimentIds = protocolExperimentIds(protocolId, 'all');
        const experimentRows = experimentIds.map(experimentId => {
          const config = curatedExperimentConfig(experimentId);
          const binding = standardAssetBindingRecord({experimentId, dashboards});
          const scenarioPath = config ? resolveCuratedExperimentScenario(config) : '';
          return {
            experimentId,
            config,
            binding,
            scenarioPath,
            hasScenario: Boolean(scenarioPath),
            hasPlan: Boolean(binding.plan || binding.planPath),
            hasResult: Boolean(binding.dashboard),
          };
        });
        const primaryExperimentId = protocolPrimaryExperimentId(protocolId, 'all');
        const primaryConfig = curatedExperimentConfig(primaryExperimentId);
        const primaryBinding = standardAssetBindingRecord({experimentId: primaryExperimentId, dashboards});
        const nextProtocol = experimentProtocolConfig(protocol?.nextProtocolId || '');
        const branches = supportingBranchRouteRecords(protocolId, dashboards);
        const readyResults = experimentRows.filter(row => row.hasResult).length;
        const readyPlans = experimentRows.filter(row => row.hasPlan).length;
        const readyScenarios = experimentRows.filter(row => row.hasScenario).length;
        const workspacePlans = plans.filter(plan => standardAssetBindingRecord({plan, dashboards}).protocolId === protocolId);
        const workspaceResults = workspacePlans.filter(plan => matchingDashboardForPlan(plan)).length;
        const status = readyResults
          ? '结果已就绪'
          : readyPlans
            ? '计划已就绪'
            : readyScenarios
              ? '可直接载入'
              : '待补入口';
        const primaryActionCode = primaryBinding.dashboard
          ? `showDashboard('${esc(primaryBinding.dashboard.path)}')`
          : primaryBinding.plan
            ? `showExperiment('${esc(primaryBinding.plan.path)}')`
            : primaryBinding.scenarioPath
              ? `prepareCuratedExperiment('${primaryExperimentId}')`
              : 'void(0)';
        const primaryActionLabel = primaryBinding.dashboard
          ? '查看代表结果'
          : primaryBinding.plan
            ? '打开代表计划'
            : primaryBinding.scenarioPath
              ? '载入入口实验'
              : '等待补场景';
        return {
          protocolId,
          protocol,
          routeMeta,
          experimentIds,
          experimentRows,
          primaryExperimentId,
          primaryConfig,
          primaryBinding,
          nextProtocol,
          branches,
          readyResults,
          readyPlans,
          readyScenarios,
          workspacePlans,
          workspaceResults,
          status,
          primaryActionCode,
          primaryActionLabel,
        };
      });
    }

    function protocolWorkspaceCardHtml(record, options = {}) {
      const mode = options.mode || 'library';
      const focusProtocolId = options.focusProtocolId || '';
      const active = focusProtocolId && focusProtocolId === record.protocolId;
      const experimentLabels = record.experimentRows.map(row => row.config?.label || row.experimentId).join(' / ');
      const branchText = record.branches.length
        ? record.branches.map(branch => branch.label).join(' / ')
        : '无';
      const representativeText = record.primaryBinding.dashboard
        ? `${record.primaryBinding.dashboard.name} · ${representativeDashboardSummary(record.primaryBinding.dashboard)}`
        : record.primaryBinding.plan
          ? `${record.primaryBinding.plan.name} · ${experimentScaleText(record.primaryBinding.plan)}`
          : '当前还没有稳定代表资产。';
      const workspaceText = mode === 'manage'
        ? `${record.workspacePlans.length} 个计划 · ${record.workspaceResults} 个已有结果`
        : mode === 'results'
          ? `${record.readyResults} 个实验已有代表结果 · ${record.readyPlans} 个实验已有计划`
          : `${record.readyPlans} 个实验已有计划 · ${record.readyResults} 个实验已有代表结果`;
      const nextProtocolText = record.nextProtocol
        ? `${record.nextProtocol.label}：${record.nextProtocol.summary}`
        : '当前协议已收束，适合回结果页沉淀结论或继续派生更细实验。';
      const focusExperimentId = record.primaryExperimentId || '';
      return `
        <div class="history-detail" style="margin-bottom:0; ${active ? 'border-color: rgba(18, 78, 120, 0.26); box-shadow: 0 10px 24px rgba(18, 78, 120, 0.08);' : ''}">
          <strong>${esc(record.protocol?.label || record.protocolId)}</strong>
          <p>${esc(record.protocol?.summary || '当前协议负责把一组实验组织成可复现研究步骤。')}</p>
          <div class="chips" style="margin-top:8px">
            <span class="chip">${esc(record.routeMeta.slot)}</span>
            <span class="chip">${esc(record.routeMeta.routeLabel)}</span>
            <span class="chip">实验 ${esc(String(record.experimentRows.length))}</span>
            <span class="chip">状态 ${esc(record.status)}</span>
          </div>
          <div class="detail-grid" style="margin-top:10px">
            <div class="detail-box"><strong>协议目标</strong><div>${esc(record.protocol?.objective || '把研究问题、步骤和结果产物组织成清晰协议。')}</div></div>
            <div class="detail-box"><strong>当前覆盖</strong><div>${esc(workspaceText)}</div></div>
            <div class="detail-box"><strong>协议实验</strong><div>${esc(experimentLabels || '待补')}</div></div>
            <div class="detail-box"><strong>关键支线</strong><div>${esc(branchText)}</div></div>
            <div class="detail-box"><strong>代表资产</strong><div>${esc(representativeText)}</div></div>
            <div class="detail-box"><strong>下一协议</strong><div>${esc(nextProtocolText)}</div></div>
          </div>
          <div class="toolbar" style="margin-top:10px">
            <button type="button" onclick="${record.primaryActionCode}" ${focusExperimentId ? '' : 'disabled'}>${esc(record.primaryActionLabel)}</button>
            <button class="secondary" type="button" onclick="${focusExperimentId ? `previewRoadmapExperiment('${esc(focusExperimentId)}')` : 'void(0)'}" ${focusExperimentId ? '' : 'disabled'}>定位入口实验</button>
            <button class="secondary" type="button" onclick="${record.nextProtocol ? `previewRoadmapExperiment('${esc(protocolPrimaryExperimentId(record.nextProtocol.id || record.protocol?.nextProtocolId || '', 'all'))}')` : 'void(0)'}" ${record.nextProtocol ? '' : 'disabled'}>定位下一协议</button>
          </div>
        </div>
      `;
    }

    function supportingBranchRouteConfig() {
      return [
        {
          id: 'sensing_chain_branch',
          label: '感知链支线',
          summary: '从统计鲁棒性里拆出传感器噪声与测量质量影响，单独判断感知链退化会先损失哪部分性能余量。',
          anchorProtocolId: 'robustness_screen',
          experimentId: 'quick_sensor_noise_sensitivity',
          demoId: 'sensor_quality_showcase',
          question: '噪声和测量质量下降时，系统先在哪类误差或通过率指标上退化？',
          next: '若感知链已经成为瓶颈，再决定是否补估计器、传感器模型或回到控制器整定。',
        },
        {
          id: 'disturbance_budget_branch',
          label: '扰动分解支线',
          summary: '从环境协议继续拆到主导扰动项，单独判断哪类外部力矩最值得优先跟踪、建模和解释。',
          anchorProtocolId: 'environment_budget',
          experimentId: 'cubesat_rw_disturbance_breakdown',
          demoId: 'disturbance_breakdown_showcase',
          question: '当前轨道与构型下，是哪一类环境扰动最先主导误差和外部力矩预算？',
          next: '若主导扰动已经明确，再接扰动-执行器权衡或更高保真环境模型。',
        },
        {
          id: 'earth_pointing_branch',
          label: '对地任务支线',
          summary: '从太阳指向任务继续扩到对地指向，单独评估 nadir 参考、任务意图和回放叙事是否仍然一致。',
          anchorProtocolId: 'mission_sequence',
          experimentId: 'cubesat_rw_earth_transition_curated',
          demoId: 'earth_transition_showcase',
          question: '从消旋进入对地任务后，时间线、参考目标和姿态回放是否仍能清楚解释任务过程？',
          next: '若对地任务已稳定，再把同一任务接到执行器边界或动量管理实验。',
        },
        {
          id: 'fault_robustness_branch',
          label: '故障鲁棒性支线',
          summary: '把统计鲁棒性推进到执行机构失效场景，单独评估单轮故障与轨道扰动共同作用下的最差工况。',
          anchorProtocolId: 'robustness_screen',
          experimentId: 'cubesat_rw_fault_seed_mc',
          demoId: '',
          question: '在单轮失效和随机扰动共同作用下，最差 run 会退化到什么程度？',
          next: '若故障边界过窄，再接故障增益权衡或执行器边界实验继续收敛。',
        },
        {
          id: 'momentum_management_branch',
          label: '动量管理支线',
          summary: '从执行器边界里继续拆出轮速回拉与长期余量问题，回答“力矩够不够”之后“轮速怎么管更合适”。',
          anchorProtocolId: 'actuator_margin',
          experimentId: 'cubesat_rw_momentum_management_sweep',
          demoId: '',
          question: '更积极的轮速管理是在改善执行器余量，还是会开始干扰姿态保持性能？',
          next: '若动量管理已明显影响姿态保持，就把结论回灌到任务实验或更高保真执行机构模型。',
        },
      ];
    }

    function supportingBranchRouteRecords(anchorProtocolId = '', dashboards = null) {
      return supportingBranchRouteConfig()
        .filter(branch => !anchorProtocolId || branch.anchorProtocolId === anchorProtocolId)
        .map(branch => {
          const experiment = curatedExperimentConfig(branch.experimentId);
          const demo = branch.demoId ? quickDemoConfig(branch.demoId) : null;
          const scenarioPath = experiment ? resolveCuratedExperimentScenario(experiment) : '';
          const representative = representativeAssetRecord({experimentId: branch.experimentId, dashboards});
          const result = representative.dashboard || representativeDashboardForExperiment(branch.experimentId);
          const plan = representative.plan || null;
          const status = result
            ? '支线结果已就绪'
            : plan
              ? '支线计划已就绪'
              : '支线待落地';
          const assetSummary = result
            ? representativeDashboardSummary(result)
            : plan
              ? `${plan.name} · ${experimentScaleText(plan)}`
              : '当前工作区还没有这条支线的计划或结果。';
          const primaryActionLabel = result ? '查看支线结果' : plan ? '打开支线计划' : '载入支线实验';
          const primaryActionCode = result
            ? `showDashboard('${esc(result.path)}')`
            : plan
              ? `showExperiment('${esc(plan.path)}')`
              : experiment && scenarioPath
                ? `prepareCuratedExperiment('${branch.experimentId}')`
                : 'void(0)';
          return {
            ...branch,
            experiment,
            demo,
            scenarioPath,
            representative,
            result,
            plan,
            status,
            assetSummary,
            availableDemo: Boolean(demo && resolveQuickDemoScenario(demo)),
            availableExperiment: Boolean(experiment && scenarioPath),
            primaryActionLabel,
            primaryActionCode,
          };
        });
    }

    function standardExperimentRouteConfig() {
      return [
        {
          id: 'baseline_closure',
          step: '第 1 步',
          label: '闭环基线',
          summary: '用最小姿态保持场景锁定一组可信的默认控制参数，先把闭环跑稳。',
          protocolId: 'baseline_closure',
          demoId: 'quick_pd_showcase',
          experimentId: 'quick_pd_gain_sweep',
          question: '最小基线里，控制器是否已经形成稳定闭环，参数区间大概在哪里？',
          next: '收紧阻尼，再进入控制器 benchmark 或严格验收。',
        },
        {
          id: 'controller_trade',
          step: '第 2 步',
          label: '控制器比较',
          summary: '在同一任务、同一环境和同一初值口径下只比较控制器差异。',
          protocolId: 'controller_trade',
          demoId: 'controller_benchmark_showcase',
          experimentId: 'quick_controller_benchmark_compare',
          question: '当前默认基线更适合选 PD 还是 LADRC？',
          next: '把更优控制器推进到轨道环境和统计鲁棒性。',
        },
        {
          id: 'robustness_screen',
          step: '第 3 步',
          label: '统计鲁棒性',
          summary: '把单次漂亮结果升级成统计结论，先看通过率、最差 run 和离散性。',
          protocolId: 'robustness_screen',
          demoId: 'sensor_quality_showcase',
          experimentId: 'quick_pd_seed_mc',
          question: '当前参数是不是只在一个 seed 上好看，还是在随机扰动下仍然稳？',
          next: '把统计问题进一步拆到感知链或故障场景。',
        },
        {
          id: 'environment_budget',
          step: '第 4 步',
          label: '环境与扰动',
          summary: '先判断轨道环境是否重要，再拆到具体扰动项和预算。',
          protocolId: 'environment_budget',
          demoId: 'orbital_environment_showcase',
          experimentId: 'quick_environment_compare',
          question: '从零扰动切到轨道环境后，哪类指标最先出现可见变化？',
          next: '继续做扰动分解，再把外场影响接到执行器余量判断。',
        },
        {
          id: 'mission_sequence',
          step: '第 5 步',
          label: '任务序列',
          summary: '把稳态保持扩成 detumble -> pointing 的任务流程，检查时间线与回放一致性。',
          protocolId: 'mission_sequence',
          demoId: 'sun_transition_showcase',
          experimentId: 'cubesat_rw_sun_transition_curated',
          question: '模式切换段是否平滑，任务时间线、参考目标和回放是否一致？',
          next: '再推进到对地任务，或者把同一任务接到执行器边界分析。',
        },
        {
          id: 'actuator_margin',
          step: '第 6 步',
          label: '执行器边界',
          summary: '在任务和环境前提明确后，再判断轮组能力、故障裕度和动量管理是否足够。',
          protocolId: 'actuator_margin',
          demoId: 'fault_wheel_showcase',
          experimentId: 'cubesat_rw_wheel_capability',
          question: '任务可行和执行器够不够是不是同一件事，真正的边界在哪？',
          next: '补执行器受限任务或动量管理，形成工程边界判断。',
        },
        {
          id: 'acceptance_gate',
          step: '第 7 步',
          label: '验收收口',
          summary: '用明确门限把“能跑”和“可验收”分开，形成稳定结果口径。',
          protocolId: 'acceptance_gate',
          demoId: 'quick_pd_showcase',
          experimentId: 'quick_pd_acceptance_gate',
          question: '当前方案是真的稳健通过，还是只是勉强能跑完？',
          next: '把统一门限回灌到后续所有计划、报告和 dashboard。',
        },
      ];
    }

    function showcaseRouteConfig() {
      return [
        {
          id: 'showcase_closure',
          step: 'Showcase 1',
          label: '最小闭环演示',
          demoId: 'quick_pd_showcase',
          experimentId: 'quick_pd_gain_sweep',
          preferredDashboardHints: ['results/platform_ui/quick_pd_showcase_3/dashboard.html', 'results/platform_ui/quick_pd_showcase_2/dashboard.html', 'results/platform_ui/quick_pd_showcase/dashboard.html'],
          audience: '第一次看平台的人',
          objective: '先证明平台能从场景、变量到结果完整跑通，并快速给出收敛和力矩差异。',
          focus: '结果摘要、Run 排行、学术图包入口',
        },
        {
          id: 'showcase_benchmark',
          step: 'Showcase 2',
          label: '控制器对比演示',
          demoId: 'controller_benchmark_showcase',
          experimentId: 'quick_controller_benchmark_compare',
          preferredDashboardHints: ['controller_benchmark_showcase'],
          audience: '老师、评审、控制算法比较场景',
          objective: '在统一任务下直接比较 PD 与 LADRC，让平台具备清楚的 benchmark 叙事。',
          focus: '控制器差异、误差-力矩对比、代表结果选择',
        },
        {
          id: 'showcase_environment',
          step: 'Showcase 3',
          label: '环境影响演示',
          demoId: 'orbital_environment_showcase',
          experimentId: 'quick_environment_compare',
          preferredDashboardHints: ['results/platform_ui/quick_environment_compare_20260615_144614/dashboard.html', 'results/platform_library/quick_environment_compare/dashboard.html'],
          audience: '需要说明工程真实性的人',
          objective: '把演示从理想闭环推进到轨道环境，让平台不只展示算法，还展示环境约束。',
          focus: 'environment 对比、扰动力矩预算、结果链路推进',
        },
        {
          id: 'showcase_mission',
          step: 'Showcase 4',
          label: '任务切换演示',
          demoId: 'sun_transition_showcase',
          experimentId: 'cubesat_rw_sun_transition_curated',
          preferredDashboardHints: ['results/platform_ui/sun_transition_showcase/dashboard.html'],
          audience: '需要看 mission / timeline / replay 联动的人',
          objective: '展示平台不只会出静态结果，还能组织任务模式切换、回放和时间线。',
          focus: 'mode timeline、姿态回放、任务叙事',
        },
        {
          id: 'showcase_margin',
          step: 'Showcase 5',
          label: '执行器边界演示',
          demoId: 'fault_wheel_showcase',
          experimentId: 'cubesat_rw_wheel_capability',
          preferredDashboardHints: ['results/platform_ui/fault_wheel_showcase/dashboard.html'],
          audience: '关注工程边界与能力裕度的人',
          objective: '说明平台可以把任务结果进一步推进到执行器 margin 判断，而不是停在误差曲线。',
          focus: '峰值力矩、饱和风险、执行器边界结论',
        },
      ];
    }

    function dashboardMatchesHint(item, hint) {
      const text = String(hint || '').trim();
      if (!text) return false;
      const path = String(item?.path || '');
      const name = String(item?.name || '');
      const experimentName = String(item?.experiment_name || '');
      return path.includes(text) || name === text || name.includes(text) || experimentName === text || experimentName.includes(text);
    }

    function findPreferredDashboardByHints(hints, dashboards = null) {
      const rows = [...(dashboards || state.workspace?.dashboards || [])].sort((a, b) => Number(b.updated_ts || 0) - Number(a.updated_ts || 0));
      for (const hint of (hints || [])) {
        const matched = rows.find(item => dashboardMatchesHint(item, hint));
        if (matched) return matched;
      }
      return null;
    }

    function showcaseRouteStages() {
      return showcaseRouteConfig().map(stage => {
        const demo = quickDemoConfig(stage.demoId);
        const experiment = curatedExperimentConfig(stage.experimentId);
        const demoPlan = quickDemoPlanRecord(demo);
        const demoDashboard = demoPlan ? matchingDashboardForPlan(demoPlan) : null;
        const representative = representativeAssetRecord({experimentId: stage.experimentId});
        const binding = standardAssetBindingRecord({experimentId: stage.experimentId});
        const preferredDashboard = findPreferredDashboardByHints(stage.preferredDashboardHints || []);
        const preferredResult = preferredDashboard || representative.dashboard || demoDashboard || null;
        const availableDemo = Boolean(resolveQuickDemoScenario(demo));
        const status = preferredResult
          ? '推荐结果已就绪'
          : representative.plan
            ? '代表计划已就绪'
            : demoPlan
              ? '标准示例计划已就绪'
              : '当前仍以示例模板为主';
        const assetSummary = preferredResult
          ? representativeDashboardSummary(preferredResult)
          : representative.plan
            ? `${representative.plan.name} · ${experimentScaleText(representative.plan)}`
            : demoPlan
              ? `${demoPlan.name} · ${experimentScaleText(demoPlan)}`
              : '当前还没有落地到稳定代表计划或代表结果。';
        const primaryActionLabel = preferredResult
          ? '打开推荐结果'
          : representative.plan
            ? '打开代表计划'
            : demoPlan
              ? '打开标准示例计划'
              : '运行这一示例';
        const primaryActionCode = preferredResult
          ? `showDashboard('${esc(preferredResult.path)}')`
          : representative.plan
            ? `showExperiment('${esc(representative.plan.path)}')`
            : demoPlan
              ? `showExperiment('${esc(demoPlan.path)}')`
              : `runQuickExample('${stage.demoId}')`;
        return {
          ...stage,
          demo,
          experiment,
          demoPlan,
          demoDashboard,
          representative,
          binding,
          preferredDashboard: preferredResult,
          availableDemo,
          status,
          assetSummary,
          primaryActionLabel,
          primaryActionCode,
        };
      });
    }

    function showcaseBranchStages() {
      return supportingBranchRouteRecords()
        .filter(branch => branch.demoId && branch.demo)
        .map(branch => {
          const anchorProtocol = experimentProtocolConfig(branch.anchorProtocolId);
          const routeMeta = protocolRouteMeta(branch.anchorProtocolId);
          const preferredDashboard = branch.result || branch.representative.dashboard || null;
          const primaryActionLabel = preferredDashboard
            ? '打开支线结果'
            : branch.plan
              ? '打开支线计划'
              : '载入支线实验';
          const primaryActionCode = preferredDashboard
            ? `showDashboard('${esc(preferredDashboard.path)}')`
            : branch.plan
              ? `showExperiment('${esc(branch.plan.path)}')`
              : `runQuickExample('${branch.demoId}')`;
          return {
            ...branch,
            anchorProtocol,
            routeMeta,
            preferredDashboard,
            primaryActionLabel,
            primaryActionCode,
          };
        });
    }

    function standardRouteStageRecords(dashboards = null) {
      const baseStages = standardExperimentRouteConfig();
      return baseStages.map((stage, index) => {
        const protocol = experimentProtocolConfig(stage.protocolId);
        const demo = quickDemoConfig(stage.demoId);
        const experiment = curatedExperimentConfig(stage.experimentId);
        const scenarioPath = resolveCuratedExperimentScenario(experiment);
        const availableDemo = Boolean(resolveQuickDemoScenario(demo));
        const availableExperiment = Boolean(scenarioPath);
        const representative = representativeAssetRecord({experimentId: stage.experimentId, dashboards});
        const result = representative.dashboard || representativeDashboardForExperiment(stage.experimentId);
        const plan = representative.plan || null;
        const nextStage = baseStages[index + 1] || null;
        const status = result
          ? '代表结果已就绪'
          : plan
            ? '代表计划待复跑'
            : '标准模板待落地';
        const assetSummary = result
          ? representativeDashboardSummary(result)
          : plan
            ? `${plan.name} · ${experimentScaleText(plan)}`
            : '当前工作区还没有这条主线的计划或结果，适合先从实验库载入主实验。';
        const primaryActionLabel = result ? '查看代表结果' : plan ? '打开代表计划' : '载入主实验';
        const primaryActionCode = result
          ? `showDashboard('${esc(result.path)}')`
          : plan
            ? `showExperiment('${esc(plan.path)}')`
            : `prepareCuratedExperiment('${stage.experimentId}')`;
        return {
          ...stage,
          protocol,
          demo,
          experiment,
          availableDemo,
          availableExperiment,
          representative,
          result,
          plan,
          nextStage,
          status,
          assetSummary,
          readiness: assetSummary,
          outputs: protocol?.outputs || 'README / index / summary_metrics / dashboard',
          reportFigures: protocol?.reportFigures || '代表误差图、力矩图和结果摘要',
          primaryActionLabel,
          primaryActionCode,
          branches: supportingBranchRouteRecords(stage.protocolId, dashboards),
        };
      });
    }

    function standardRouteWorkbenchHtml(stages, options = {}) {
      const rows = Array.isArray(stages) ? stages : [];
      if (!rows.length) {
        return `
          <div class="callout" style="margin:10px 0 0">
            <strong>${esc(options.title || '标准实验主线')}</strong>
            <p>当前还没有可展示的标准主线。</p>
          </div>
        `;
      }
      const title = options.title || '标准实验主线';
      const lead = options.lead || '把标准实验、代表计划和代表结果放回同一条主线里，切页后仍然能保持统一的判断结构。';
      const mode = options.mode || 'library';
      const focusExperimentId = options.focusExperimentId || '';
      const resultReadyCount = rows.filter(stage => stage.result).length;
      const planReadyCount = rows.filter(stage => stage.plan).length;
      const branchCount = rows.reduce((sum, stage) => sum + (Array.isArray(stage.branches) ? stage.branches.length : 0), 0);
      const stageCards = rows.map(stage => {
        const isCurrent = Boolean(focusExperimentId) && focusExperimentId === stage.experimentId;
        const branches = Array.isArray(stage.branches) ? stage.branches : [];
        const primaryBranch = branches[0] || null;
        const chips = [
          stage.status,
          `主实验 ${stage.experiment?.label || stage.experimentId}`,
          stage.nextStage ? `下一环 ${stage.nextStage.label}` : '当前主线收束',
        ];
        if (isCurrent) chips.unshift('当前主线');
        if (branches.length) chips.push(`${branches.length} 条关键支线`);
        let secondaryActions = '';
        if (mode === 'results') {
          secondaryActions = `
            <button class="secondary" type="button" onclick="runQuickExample('${stage.demoId}')">运行演示</button>
            <button class="secondary" type="button" onclick="previewRoadmapExperiment('${stage.experimentId}')">定位实验</button>
            <button class="secondary" type="button" onclick="${stage.nextStage ? `previewRoadmapExperiment('${stage.nextStage.experimentId}')` : `switchResultsView('overview')`}">${esc(stage.nextStage ? `看下一环 ${stage.nextStage.label}` : '回到结果总览')}</button>
            <button class="secondary" type="button" onclick="${primaryBranch ? `previewRoadmapExperiment('${primaryBranch.experimentId}')` : 'void(0)'}" ${primaryBranch ? '' : 'disabled'}>看关键支线</button>
          `;
        } else if (mode === 'manage') {
          secondaryActions = `
            <button class="secondary" type="button" onclick="${stage.plan ? `showExperiment('${esc(stage.plan.path)}')` : 'void(0)'}" ${stage.plan ? '' : 'disabled'}>打开代表计划</button>
            <button class="secondary" type="button" onclick="${stage.result ? `showDashboard('${esc(stage.result.path)}')` : 'void(0)'}" ${stage.result ? '' : 'disabled'}>查看代表结果</button>
            <button class="secondary" type="button" onclick="previewRoadmapExperiment('${stage.experimentId}')">定位实验</button>
            <button class="secondary" type="button" onclick="${primaryBranch ? `previewRoadmapExperiment('${primaryBranch.experimentId}')` : 'void(0)'}" ${primaryBranch ? '' : 'disabled'}>看关键支线</button>
          `;
        } else {
          secondaryActions = `
            <button class="secondary" type="button" onclick="${stage.availableDemo ? `runQuickExample('${stage.demoId}')` : 'void(0)'}" ${stage.availableDemo ? '' : 'disabled'}>运行演示</button>
            <button class="secondary" type="button" onclick="${stage.availableExperiment ? `prepareCuratedExperiment('${stage.experimentId}')` : 'void(0)'}" ${stage.availableExperiment ? '' : 'disabled'}>载入主实验</button>
            <button class="secondary" type="button" onclick="previewRoadmapExperiment('${stage.experimentId}')">定位实验</button>
            <button class="secondary" type="button" onclick="${stage.result ? `showDashboard('${esc(stage.result.path)}')` : 'void(0)'}" ${stage.result ? '' : 'disabled'}>查看结果</button>
            <button class="secondary" type="button" onclick="${primaryBranch ? `previewRoadmapExperiment('${primaryBranch.experimentId}')` : 'void(0)'}" ${primaryBranch ? '' : 'disabled'}>看关键支线</button>
          `;
        }
        return `
          <div class="history-detail" style="margin-bottom:0">
            <strong>${esc(stage.step)} · ${esc(stage.label)}</strong>
            <p>${esc(stage.summary)}</p>
            <div class="chips" style="margin-top:8px">
              ${chips.map(chip => `<span class="chip">${esc(chip)}</span>`).join('')}
            </div>
            <div class="detail-grid" style="margin-top:10px">
              <div class="detail-box">
                <strong>当前代表资产</strong>
                <div>${esc(stage.assetSummary)}</div>
              </div>
              <div class="detail-box">
                <strong>现在回答的问题</strong>
                <div>${esc(stage.question)}</div>
              </div>
              <div class="detail-box">
                <strong>建议交付物</strong>
                <div>${esc(stage.outputs)}</div>
              </div>
              <div class="detail-box">
                <strong>下一步</strong>
                <div>${esc(stage.next)}</div>
              </div>
              <div class="detail-box">
                <strong>关键支线</strong>
                <div>${branches.length
                  ? esc(branches.map(branch => `${branch.label} · ${branch.status}`).join('；'))
                  : '当前主线没有单独抽出的关键支线。'}</div>
              </div>
            </div>
            <div class="toolbar" style="margin-top:10px">
              <button type="button" onclick="${stage.primaryActionCode}">${esc(stage.primaryActionLabel)}</button>
              ${secondaryActions}
            </div>
          </div>
        `;
      }).join('');
      return `
        <div class="history-detail" style="margin:10px 0 0">
          <strong>${esc(title)}</strong>
          <p>${esc(lead)}</p>
          <div class="summary-grid" style="margin-top:10px">
            <div class="summary-card"><span>主线环节</span><strong>${rows.length}</strong></div>
            <div class="summary-card"><span>已有代表结果</span><strong>${resultReadyCount}</strong></div>
            <div class="summary-card"><span>已有代表计划</span><strong>${planReadyCount}</strong></div>
            <div class="summary-card"><span>关键支线</span><strong>${branchCount}</strong></div>
            <div class="summary-card"><span>当前建议</span><strong>${esc(resultReadyCount ? '沿主线继续收敛' : '先补代表结果')}</strong></div>
          </div>
          <div class="ribbon" style="margin-top:10px">
            ${rows.map(stage => `<span class="ribbon-step">${esc(stage.label)}</span>`).join('')}
          </div>
          <div class="intro-grid" style="margin-top:10px">
            ${stageCards}
          </div>
        </div>
      `;
    }

    function renderStandardExperimentRoute() {
      const stages = standardRouteStageRecords();
      const readyResults = stages.filter(stage => stage.representative.dashboard).length;
      const readyPlans = stages.filter(stage => stage.representative.plan).length;
      if (overviewStandardRoute) {
        const showcaseStages = showcaseRouteStages();
        const branchShowcases = showcaseBranchStages();
        overviewStandardRoute.innerHTML = `
          <div class="history-detail" style="margin-bottom:0">
            <strong>平台标准实验主线</strong>
            <p>这条主线把当前平台最值得优先展示和继续扩展的实验收成固定顺序。先跑通基线，再往 benchmark、统计、环境、任务和执行器边界扩展，最后用统一验收收口。</p>
            <div class="detail-grid" style="margin-top:10px">
              <div class="detail-box"><strong>主线阶段</strong><div>${stages.length} 个标准环节，覆盖从闭环到验收的完整研究链。</div></div>
              <div class="detail-box"><strong>结果覆盖</strong><div>${readyResults} / ${stages.length} 个环节已有代表结果，可直接用于平台演示或结果复查。</div></div>
              <div class="detail-box"><strong>计划覆盖</strong><div>${readyPlans} / ${stages.length} 个环节已有代表计划，可直接继续编辑或复跑。</div></div>
              <div class="detail-box"><strong>成熟范式</strong><div>先固定实验协议，再组织计划、运行和报告，和成熟仿真平台的研究工作流保持同向。</div></div>
            </div>
            <div class="ribbon" style="margin-top:10px">
              ${stages.map(stage => `<span class="ribbon-step">${esc(stage.label)}</span>`).join('')}
            </div>
            <div class="flow-sequence" style="margin-top:10px">
              ${stages.map(stage => `
                <div class="flow-node ${stage.representative.dashboard ? 'ok' : stage.representative.plan ? 'accent' : 'warm'}">
                  <small>${esc(stage.step)}</small>
                  <strong>${esc(stage.label)}</strong>
                  <p>${esc(stage.summary)}</p>
                </div>
              `).join('')}
            </div>
            <div class="history-detail" style="margin:10px 0 0">
              <strong>首开 Showcase 路径</strong>
              <p>如果首页只演示一条最短路径，建议按“闭环 -> benchmark -> 环境 -> mission -> margin”五步走。这样既能讲平台能力，又不会把首次展示拉得过长。</p>
              <div class="ribbon" style="margin-top:10px">
                ${showcaseStages.map(stage => `<span class="ribbon-step">${esc(stage.label)}</span>`).join('')}
              </div>
              <div class="detail-grid" style="margin-top:10px">
                ${showcaseStages.map(stage => `
                  <div class="detail-box">
                    <strong>${esc(stage.step)}</strong>
                    <div>${esc(stage.objective)}</div>
                    <div class="subtle" style="margin-top:8px">${esc(`重点看：${stage.focus}`)}</div>
                    <div class="subtle" style="margin-top:8px">${esc(`当前入口：${stage.assetSummary}`)}</div>
                  </div>
                `).join('')}
              </div>
              <div class="intro-grid" style="margin-top:10px">
                ${showcaseStages.map(stage => `
                  <div class="history-detail" style="margin-bottom:0">
                    <strong>${esc(stage.label)}</strong>
                    <p>${esc(`适合对象：${stage.audience}`)}</p>
                    <div class="chips" style="margin-top:8px">
                      <span class="chip">${esc(stage.status)}</span>
                      <span class="chip">${esc(stage.focus)}</span>
                    </div>
                    <div class="toolbar" style="margin-top:10px">
                      <button type="button" onclick="${stage.primaryActionCode}">${esc(stage.primaryActionLabel)}</button>
                      <button class="secondary" type="button" onclick="${stage.availableDemo ? `prepareQuickExample('${stage.demoId}')` : 'void(0)'}" ${stage.availableDemo ? '' : 'disabled'}>载入示例</button>
                      <button class="secondary" type="button" onclick="previewRoadmapExperiment('${stage.experimentId}')">定位主实验</button>
                    </div>
                  </div>
                `).join('')}
              </div>
            </div>
            <div class="history-detail" style="margin:10px 0 0">
              <strong>关键支线补充入口</strong>
              <p>主线始终保持 7 步不变。如果要把某一步继续讲深，优先从这些支线入口进入，而不是把支线混成新的主线环节。</p>
              <div class="detail-grid" style="margin-top:10px">
                ${branchShowcases.length ? branchShowcases.map(branch => `
                  <div class="detail-box">
                    <strong>${esc(branch.label)}</strong>
                    <div>${esc(branch.summary)}</div>
                    <div class="subtle" style="margin-top:8px">${esc(`锚点：${branch.anchorProtocol?.label || branch.routeMeta.routeLabel}`)}</div>
                    <div class="subtle" style="margin-top:8px">${esc(`当前入口：${branch.assetSummary}`)}</div>
                  </div>
                `).join('') : '<div class="detail-box"><strong>当前状态</strong><div>当前还没有补充支线展示入口。</div></div>'}
              </div>
            </div>
          </div>
        `;
      }
      const showcaseStages = showcaseRouteStages();
      const branchShowcases = showcaseBranchStages();
      if (libraryStandardRoute) {
        libraryStandardRoute.innerHTML = `
          <div class="history-timeline">
            <h3>标准实验路径</h3>
            <div class="history-detail" style="margin-bottom:0">
              <strong>推荐使用方式</strong>
              <p>不要先从空白 JSON 开始。先沿这条标准路径选一个环节，直接运行示例或载入主实验，再在该环节的变量、任务和验收上做派生。</p>
              <div class="detail-grid" style="margin-top:10px">
                <div class="detail-box"><strong>首轮演示</strong><div>推荐先跑闭环基线、控制器比较和环境对比，三步就能把平台的“场景 -> 实验 -> 结果”链路讲清楚。</div></div>
                <div class="detail-box"><strong>任务与工程边界</strong><div>太阳 / 对地任务和执行器边界适合第二轮演示，用来说明平台不只会做稳态曲线，还能组织 mission 和 margin。</div></div>
                <div class="detail-box"><strong>统一收口</strong><div>最后用验收门限把结果回收到统一口径，方便 dashboard、报告和批量筛选继续复用。</div></div>
              </div>
            </div>
            <div class="history-detail" style="margin-top:10px; margin-bottom:0">
              <strong>首开 Showcase 路径</strong>
              <p>如果你是第一次向别人展示平台，不需要把所有主线都讲完。先按下面五步演示，每一步都对应一个清楚的展示对象和讲述重点。</p>
              <div class="flow-sequence" style="margin-top:10px">
                ${showcaseStages.map(stage => `
                  <div class="flow-node ${stage.representative.dashboard ? 'ok' : stage.representative.plan ? 'accent' : 'warm'}">
                    <small>${esc(stage.step)}</small>
                    <strong>${esc(stage.label)}</strong>
                    <p>${esc(stage.objective)}</p>
                  </div>
                `).join('')}
              </div>
              <div class="detail-grid" style="margin-top:10px">
                ${showcaseStages.map(stage => `
                  <div class="detail-box">
                    <strong>${esc(stage.label)}</strong>
                    <div>${esc(`适合对象：${stage.audience}`)}</div>
                    <div class="subtle" style="margin-top:8px">${esc(`重点看：${stage.focus}`)}</div>
                    <div class="subtle" style="margin-top:8px">${esc(`当前入口：${stage.assetSummary}`)}</div>
                  </div>
                `).join('')}
              </div>
              <div class="intro-grid" style="margin-top:10px">
                ${showcaseStages.map(stage => `
                  <div class="history-detail" style="margin-bottom:0">
                    <strong>${esc(stage.step)} · ${esc(stage.label)}</strong>
                    <p>${esc(stage.objective)}</p>
                    <div class="chips" style="margin-top:8px">
                      <span class="chip">${esc(stage.status)}</span>
                      <span class="chip">对象 ${esc(stage.audience)}</span>
                    </div>
                    <div class="toolbar" style="margin-top:10px">
                      <button type="button" onclick="${stage.primaryActionCode}">${esc(stage.primaryActionLabel)}</button>
                      <button class="secondary" type="button" onclick="${stage.availableDemo ? `runQuickExample('${stage.demoId}')` : 'void(0)'}" ${stage.availableDemo ? '' : 'disabled'}>运行示例</button>
                      <button class="secondary" type="button" onclick="previewRoadmapExperiment('${stage.experimentId}')">定位主实验</button>
                    </div>
                  </div>
                `).join('')}
              </div>
            </div>
            <div class="history-detail" style="margin-top:10px; margin-bottom:0">
              <strong>关键支线补充演示</strong>
              <p>如果主线已经讲清楚，需要进一步深入感知链、扰动解释或对地任务，可以从这些支线入口继续推进，而不用改写整条主线。</p>
              <div class="intro-grid" style="margin-top:10px">
                ${branchShowcases.length ? branchShowcases.map(branch => `
                  <div class="history-detail" style="margin-bottom:0">
                    <strong>${esc(branch.label)}</strong>
                    <p>${esc(branch.summary)}</p>
                    <div class="chips" style="margin-top:8px">
                      <span class="chip">${esc(branch.status)}</span>
                      <span class="chip">锚点 ${esc(branch.anchorProtocol?.label || branch.routeMeta.routeLabel)}</span>
                    </div>
                    <div class="toolbar" style="margin-top:10px">
                      <button type="button" onclick="${branch.primaryActionCode}">${esc(branch.primaryActionLabel)}</button>
                      <button class="secondary" type="button" onclick="${branch.demoId ? `runQuickExample('${branch.demoId}')` : 'void(0)'}" ${branch.demoId ? '' : 'disabled'}>运行示例</button>
                      <button class="secondary" type="button" onclick="previewRoadmapExperiment('${branch.experimentId}')">定位支线实验</button>
                    </div>
                  </div>
                `).join('') : '<div class="detail-box"><strong>当前状态</strong><div>当前还没有补充支线展示入口。</div></div>'}
              </div>
            </div>
            ${standardRouteWorkbenchHtml(stages, {
              mode: 'library',
              title: '标准实验路径总览',
              lead: '实验库、计划管理和结果层共用同一条主线卡组。这里保留模板侧入口，方便先选环节，再去载入主实验、运行演示或打开代表结果。',
            })}
          </div>
        `;
      }
    }

    function renderLibraryProtocolMap() {
      if (!libraryProtocolMap) return;
      const records = protocolWorkspaceMapRecords();
      const experimentCount = records.reduce((sum, record) => sum + record.experimentRows.length, 0);
      const planCount = records.reduce((sum, record) => sum + record.readyPlans, 0);
      const resultCount = records.reduce((sum, record) => sum + record.readyResults, 0);
      const branchCount = records.reduce((sum, record) => sum + record.branches.length, 0);
      const cards = records.map(record => protocolWorkspaceCardHtml(record, {mode: 'library'})).join('');
      const quickRows = records.flatMap(record => record.experimentRows.map(row => {
        const status = row.hasResult
          ? '结果已就绪'
          : row.hasPlan
            ? '计划已就绪'
            : row.hasScenario
              ? '可直接载入'
              : '待补场景';
        const actionCode = row.binding.dashboard
          ? `showDashboard('${esc(row.binding.dashboard.path)}')`
          : row.binding.plan
            ? `showExperiment('${esc(row.binding.plan.path)}')`
            : row.scenarioPath
              ? `prepareCuratedExperiment('${row.experimentId}')`
              : 'void(0)';
        const actionLabel = row.binding.dashboard
          ? '结果'
          : row.binding.plan
            ? '计划'
            : row.scenarioPath
              ? '载入'
              : '等待';
        return `
          <tr>
            <td title="${esc(row.experimentId)}">${esc(row.config?.label || row.experimentId)}</td>
            <td title="${esc(record.protocol?.label || record.protocolId)}">${esc(record.protocol?.label || record.protocolId)}</td>
            <td title="${esc(row.config?.question || '—')}">${esc(row.config?.question || '—')}</td>
            <td title="${esc(row.config?.variable || '—')}">${esc(row.config?.variable || '—')}</td>
            <td title="${esc(status)}">${esc(status)}</td>
            <td><button type="button" onclick="${actionCode}" ${actionCode === 'void(0)' ? 'disabled' : ''}>${esc(actionLabel)}</button></td>
          </tr>
        `;
      })).join('');
      libraryProtocolMap.innerHTML = `
        <div class="history-detail" style="margin-bottom:12px">
          <strong>协议阶段总览</strong>
          <p>把“协议阶段 -> 代表实验 -> 当前资产状态 -> 下一协议”压成一张地图，先建立整体实验认知，再进入具体模板或示例。</p>
          <div class="summary-grid" style="margin-top:10px">
            <div class="summary-card"><span>协议阶段</span><strong>${records.length}</strong></div>
            <div class="summary-card"><span>成熟实验</span><strong>${experimentCount}</strong></div>
            <div class="summary-card"><span>已有计划</span><strong>${planCount}</strong></div>
            <div class="summary-card"><span>已有结果</span><strong>${resultCount}</strong></div>
            <div class="summary-card"><span>关键支线</span><strong>${branchCount}</strong></div>
          </div>
        </div>
        <div class="intro-grid" style="margin-top:10px">
          ${cards}
        </div>
        <div class="history-detail" style="margin-top:12px; margin-bottom:0">
          <strong>实验用途速查</strong>
          <p>这里直接回答“这个平台里到底有哪些实验，它们各自用来做什么”。选中后可直接载入、打开计划或看结果。</p>
          <table style="margin-top:10px">
            <thead>
              <tr>
                <th>实验</th>
                <th>协议</th>
                <th>主要问题</th>
                <th>核心变量</th>
                <th>当前状态</th>
                <th>入口</th>
              </tr>
            </thead>
            <tbody>${quickRows}</tbody>
          </table>
        </div>
      `;
    }

    function resultsStandardRouteMarkup(dashboards = []) {
      return standardRouteWorkbenchHtml(standardRouteStageRecords(dashboards), {
        mode: 'results',
        title: '标准实验主线看板',
        lead: '结果首页也按同一条标准实验路径组织。这样看到的不只是“最近结果”，而是“这条研究主线推进到哪一步、哪一步已有代表结果、下一步该接什么”。',
      });
    }

    function renderOverviewVisualMaps({scenarios, experiments, archivedExperiments, dashboards, latestPlan, latestResult, pendingPlans, attentionResults}) {
      if (overviewPlatformMap) {
        overviewPlatformMap.innerHTML = `
          <div class="flow-node accent">
            <small>Step 1</small>
            <strong>场景基线</strong>
            <p>当前工作区里有 ${scenarios.length} 个场景。先固定动力学、控制器、环境和初始状态，再开始设计实验。</p>
          </div>
          <div class="flow-node">
            <small>Step 2</small>
            <strong>实验计划</strong>
            <p>当前已有 ${experiments.length} 份活动计划。把变量、任务模板、Monte Carlo 和验收门限固化成标准资产。</p>
          </div>
          <div class="flow-node warm">
            <small>Step 3</small>
            <strong>批量运行</strong>
            <p>${pendingPlans.length ? `还有 ${pendingPlans.length} 份计划待运行。` : '当前待运行计划已经清空。'} 运行后平台会自动整理目录、指标表和报告。</p>
          </div>
          <div class="flow-node ok">
            <small>Step 4</small>
            <strong>结果报告</strong>
            <p>当前已有 ${dashboards.length} 份结果界面${latestResult ? `，最新通过率 ${Math.round((latestResult.acceptance_rate || 0) * 1000) / 10}%` : ''}。</p>
          </div>
          <div class="flow-node">
            <small>Step 5</small>
            <strong>对比与回放</strong>
            <p>${attentionResults.length ? `有 ${attentionResults.length} 份结果需要优先诊断。` : '当前可直接进入对比、回放和代表结果整理。'} 把结论回接到下一轮实验。</p>
          </div>
        `;
      }
      if (overviewArchitectureMap) {
        overviewArchitectureMap.innerHTML = `
          <strong>平台分层图</strong>
          <p>按成熟平台思路，把工作区、实验计划、运行时和结果层次拆开，而不是把逻辑塞在一个脚本里。</p>
          <div class="ribbon">
            <span class="ribbon-step">Project</span>
            <span class="ribbon-step">Scenario</span>
            <span class="ribbon-step">ExperimentPlan</span>
            <span class="ribbon-step">Runner</span>
            <span class="ribbon-step">Report</span>
            <span class="ribbon-step">Replay</span>
          </div>
          <p>${latestPlan ? `当前最新计划是 ${latestPlan.name}。` : '当前还没有活动计划。'} ${archivedExperiments.length ? `另有 ${archivedExperiments.length} 份归档计划保留历史版本。` : '归档区当前为空。'}</p>
        `;
      }
      if (overviewExperimentMap) {
        overviewExperimentMap.innerHTML = `
          <strong>实验推进图</strong>
          <p>优先沿一条标准研究主线推进：先闭环基线，再做 benchmark / 鲁棒性，再扩环境、任务和执行器边界。</p>
          <div class="ribbon">
            <span class="ribbon-step">闭环基线</span>
            <span class="ribbon-step">控制器 benchmark</span>
            <span class="ribbon-step">鲁棒性</span>
            <span class="ribbon-step">环境 / 扰动</span>
            <span class="ribbon-step">任务模式</span>
            <span class="ribbon-step">执行器边界</span>
            <span class="ribbon-step">验收口径</span>
          </div>
          <p>${latestResult ? `当前最新结果是 ${latestResult.name}。` : '当前还没有结果界面。'} 这一排更适合拿来判断下一轮该继续补哪类实验，而不是单看某一个 JSON 文件。</p>
        `;
      }
    }

    function renderBuilderVisualCanvas() {
      if (!builderVisualCanvas) return;
      const scenario = state.workspace?.scenarios?.find(item => item.path === builderScenario.value);
      const scenarioName = scenario?.name || '未选场景';
      const templateId = currentBuilderTemplateId();
      const templateConfig = experimentTemplateConfig(templateId);
      const protocol = experimentProtocolConfig(protocolIdForTemplate(templateId));
      const missionLabel = builderMission.value === 'detumble_then_hold' ? '消旋后保持' : '单模式保持';
      const presetConfig = sweepPresetConfig(builderSweepPreset.value || 'custom');
      const acceptanceConfig = acceptancePresetConfig(builderAcceptancePreset.value || 'standard_hold');
      const outputRoot = String(builderOutput.value || '').trim() || `results/platform_ui/${(builderName.value || scenarioName || 'experiment').trim().replace(/\\s+/g, '_')}`;
      builderVisualCanvas.innerHTML = `
        <div class="flow-node accent">
          <small>问题</small>
          <strong>${esc(builderName.value || templateConfig?.label || '当前实验')}</strong>
          <p>${esc(String(builderDescription.value || '').trim() || protocol?.objective || '当前实验先围绕一个清楚的研究问题组织。')}</p>
        </div>
        <div class="flow-node">
          <small>场景</small>
          <strong>${esc(scenarioName)}</strong>
          <p>${esc(scenario ? `${fmt(scenario.duration_s)} s / dt ${fmt(scenario.dt_s)} · ${scenario.system || 'system'} / ${scenario.controller || 'controller'}` : '先固定基线物理、控制器和环境。')}</p>
        </div>
        <div class="flow-node">
          <small>变量</small>
          <strong>${esc(presetConfig.label || '单场景')}</strong>
          <p>${esc(inferSweepMeaning(builderSweepPath.value).replace('当前', '这里'))}</p>
        </div>
        <div class="flow-node warm">
          <small>任务</small>
          <strong>${esc(missionLabel)}</strong>
          <p>${esc(`${modeExplanation(builderMode.value)} 参考目标 ${builderReference.value || 'body_zero'}。`)}</p>
        </div>
        <div class="flow-node ok">
          <small>验收</small>
          <strong>${esc(acceptanceConfig.label)}</strong>
          <p>${esc(`末端 ${fmt(builderAcceptFinal.value)} deg / RMS ${fmt(builderAcceptRms.value)} deg / 力矩 ${fmt(builderAcceptTorque.value)} Nm`)}</p>
        </div>
        <div class="flow-node">
          <small>结果</small>
          <strong>${esc(outputRoot)}</strong>
          <p>${esc(protocol ? `按 ${protocol.label} 继续推进，重点产出 ${protocol.outputs}。` : '生成 README、index、summary_metrics 和 dashboard。')}</p>
        </div>
      `;
    }

    function renderBuilderProtocolSheet() {
      if (!builderProtocolSheet) return;
      const templateId = currentBuilderTemplateId();
      const templateConfig = experimentTemplateConfig(templateId);
      const protocolId = protocolIdForTemplate(templateId);
      const protocol = experimentProtocolConfig(protocolId);
      if (!protocol) {
        builderProtocolSheet.innerHTML = `
          <strong>标准实验协议卡</strong>
          <p>选择模板后，这里会同步展示推荐变量、验收口径、建议结果图和代表实验入口。</p>
        `;
        return;
      }
      const presetConfig = sweepPresetConfig(builderSweepPreset.value || 'custom');
      const acceptanceConfig = acceptancePresetConfig(builderAcceptancePreset.value || 'standard_hold');
      const sweepValues = parseBuilderValues(builderSweepValues.value);
      const secondSweepValues = parseBuilderValues(builderSecondSweepValues.value);
      const secondSweepEnabled = Boolean(String(builderSecondSweepPath.value || '').trim() && secondSweepValues.length);
      const secondPresetConfig = sweepPresetConfig(builderSecondSweepPreset.value || 'custom');
      const representativeId = protocolPrimaryExperimentId(protocolId, 'all');
      const representativeConfig = curatedExperimentConfig(representativeId);
      const representativePlanPath = representativeConfig ? resolveCuratedExperimentPlan(representativeConfig) : '';
      const representativePlan = representativePlanPath
        ? [...(state.workspace?.experiments || [])].find(plan => plan.path === representativePlanPath) || null
        : null;
      const representativeDashboard = representativePlan ? matchingDashboardForPlan(representativePlan) : null;
      const protocolBranches = supportingBranchRouteRecords(protocolId);
      const primaryBranch = protocolBranches[0] || null;
      const nextProtocol = experimentProtocolConfig(protocol?.nextProtocolId || '');
      const nextProtocolExperimentId = protocolPrimaryExperimentId(protocol?.nextProtocolId || '', 'all');
      const academicMeta = experimentAcademicMeta(representativeId, representativeConfig || {theme: ''});
      const standardMeta = experimentStandardCardMeta(representativeId, representativeConfig || templateConfig || {planName: templateId, theme: ''}, academicMeta);
      const assetBinding = standardAssetBindingRecord({experimentId: representativeId});
      const variableText = [
        `${presetConfig.label || '当前变量'}${sweepValues.length ? ` · ${sweepValues.join('、')}` : ''}`,
        secondSweepEnabled ? `${secondPresetConfig.label || builderSecondSweepPath.value} · ${secondSweepValues.join('、')}` : '',
        Number(builderMcSamples.value || 0) > 0 ? `Monte Carlo ${builderMcSamples.value} 次` : '',
      ].filter(Boolean).join(' / ');
      const acceptanceText = `${acceptanceConfig.label} · 末端 <= ${fmt(builderAcceptFinal.value)} deg / RMS <= ${fmt(builderAcceptRms.value)} deg / 峰值力矩 <= ${fmt(builderAcceptTorque.value)} Nm`;
      const representativeText = representativeConfig
        ? `${representativeConfig.label}${representativeDashboard ? ` · 已有代表结果 ${representativeDashboard.name}` : ' · 当前还没有代表结果'}`
        : '当前协议还没有绑定代表实验。';
      const briefMeta = experimentBriefMeta({
        experimentId: representativeId,
        config: representativeConfig || {planName: templateId, theme: templateConfig?.label || protocol?.label || ''},
        detailMeta: representativeId ? experimentLibraryDetailMeta(representativeId, representativeConfig) : null,
        academicMeta,
        standardMeta,
        question: String(builderDescription.value || '').trim() || representativeConfig?.question || protocol?.objective,
        scenarioPath: builderScenario.value,
        templateConfig,
        sweepPath: builderSweepPath.value,
        secondSweepPath: secondSweepEnabled ? builderSecondSweepPath.value : '',
        sweepRows: [
          {
            label: presetConfig.label || builderSweepPath.value || '当前变量',
            path: builderSweepPath.value,
            count: sweepValues.length || 1,
            values: sweepValues.map(value => String(value)),
          },
          ...(secondSweepEnabled ? [{
            label: secondPresetConfig.label || builderSecondSweepPath.value || '第二变量',
            path: builderSecondSweepPath.value,
            count: secondSweepValues.length || 1,
            values: secondSweepValues.map(value => String(value)),
          }] : []),
        ],
        mission: builderMission.value,
        mode: builderMode.value,
        reference: builderReference.value,
        detumbleS: templateConfig?.detumbleS,
        acceptanceRows: [
          {label: '末端误差', value: `<= ${fmt(builderAcceptFinal.value)} deg`},
          {label: 'RMS 误差', value: `<= ${fmt(builderAcceptRms.value)} deg`},
          {label: '峰值力矩', value: `<= ${fmt(builderAcceptTorque.value)} Nm`},
        ],
        acceptanceConfig,
        mcSamples: Number(builderMcSamples.value || 0),
        nextSummary: nextProtocol ? `${nextProtocol.label}：${nextProtocol.summary}` : standardMeta.derivedPlan,
        figuresSummary: academicMeta.plots || standardMeta.figurePackage,
      });
      const readingTask = experimentReadingTaskMeta({
        experimentId: representativeId,
        config: representativeConfig || {planName: templateId, theme: templateConfig?.label || protocol?.label || ''},
        detailMeta: representativeId ? experimentLibraryDetailMeta(representativeId, representativeConfig) : null,
        academicMeta,
        standardMeta,
        deliverables: protocol?.outputs || standardMeta.figurePackage,
        nextAction: nextProtocol ? `${nextProtocol.label}：${nextProtocol.summary}` : standardMeta.derivedPlan,
      });
      builderProtocolSheet.innerHTML = `
        ${experimentBriefHtml(briefMeta, {
          title: '实验说明卡',
          lead: '创建器先把模板、变量、任务和验收压成统一说明，再决定是否生成计划。这样建实验时看到的不是零散字段。',
          compact: true,
        })}
        ${experimentReadingTaskHtml(readingTask, {
          title: '结果阅读任务书',
          lead: '创建器里直接告诉你实验跑完后先看什么、再看什么，避免生成计划后又回头猜读图顺序。',
          compact: true,
        })}
        <div class="history-detail" style="margin-top:10px; margin-bottom:0">
          <strong>标准实验协议卡</strong>
          <p>这里把实验库中的协议语义直接带到创建器里，确保“模板选择 -> 变量设计 -> 验收 -> 结果阅读”是一条连贯研究路径。</p>
          <div class="detail-grid" style="margin-top:10px">
            <div class="detail-box"><strong>协议目标</strong><div>${esc(protocol.objective || '当前协议用于把实验放回长期研究主线里理解。')}</div></div>
            <div class="detail-box"><strong>推荐变量设计</strong><div>${esc(variableText || '当前按单场景单配置运行。')}</div></div>
            <div class="detail-box"><strong>推荐验收口径</strong><div>${esc(acceptanceText)}</div><div class="subtle" style="margin-top:8px">${esc(standardMeta.acceptance)}</div></div>
            <div class="detail-box"><strong>建议结果图</strong><div>${esc(academicMeta.plots || 'Run 排行、误差曲线和关键指标摘要。')}</div></div>
            <div class="detail-box"><strong>推荐结果图包</strong><div>${esc(standardMeta.figurePackage)}</div></div>
            <div class="detail-box"><strong>代表实验入口</strong><div>${esc(representativeText)}</div></div>
            <div class="detail-box"><strong>下一条协议</strong><div>${esc(nextProtocol ? `${nextProtocol.label}：${nextProtocol.summary}` : '当前协议已经收束，适合回到结果页整理代表结论。')}</div></div>
            <div class="detail-box"><strong>关键支线</strong><div>${esc(protocolBranches.length ? protocolBranches.map(branch => `${branch.label}：${branch.summary}`).join('；') : '当前协议没有单独抽出的关键支线。')}</div></div>
            <div class="detail-box"><strong>适用前提</strong><div>${esc(protocol.prerequisites || academicMeta.prerequisites || '先固定场景、参考和任务，再改变目标变量。')}</div></div>
            <div class="detail-box"><strong>派生命名建议</strong><div>${esc(standardMeta.derivedPlan)}</div></div>
            <div class="detail-box"><strong>协议成熟度</strong><div>${esc(standardMeta.maturity)}</div></div>
            <div class="detail-box"><strong>资产定位</strong><div>${esc(standardMeta.assetRole)}</div></div>
            <div class="detail-box"><strong>成熟范式参考</strong><div>${esc(protocol.matureReference || '当前协议对应成熟平台中的标准研究流程。')}</div></div>
          </div>
          ${standardAssetBindingHtml(assetBinding, {
            title: '创建器主线资产链',
            showToolbar: false,
          })}
          <div class="toolbar" style="margin-top:10px">
            <button type="button" onclick="${representativeId ? `focusExperimentLibrary('${esc(representativeId)}')` : 'void(0)'}" ${representativeId ? '' : 'disabled'}>在实验库中定位</button>
            <button class="secondary" type="button" onclick="${representativeId ? `prepareCuratedExperiment('${esc(representativeId)}')` : 'void(0)'}" ${representativeId ? '' : 'disabled'}>载入当前协议模板</button>
            <button class="secondary" type="button" onclick="${protocolId ? `prepareNextProtocolExperiment('${esc(protocolId)}', '创建器协议卡：')` : 'void(0)'}" ${(protocolId && nextProtocolExperimentId) ? '' : 'disabled'}>载入下一协议模板</button>
            <button class="secondary" type="button" onclick="${representativePlanPath ? `showExperiment('${esc(representativePlanPath)}')` : 'void(0)'}" ${representativePlanPath ? '' : 'disabled'}>打开代表计划</button>
            <button class="secondary" type="button" onclick="${representativeDashboard ? `showDashboard('${esc(representativeDashboard.path)}')` : 'void(0)'}" ${representativeDashboard ? '' : 'disabled'}>查看代表结果</button>
            <button class="secondary" type="button" onclick="${nextProtocolExperimentId ? `previewRoadmapExperiment('${esc(nextProtocolExperimentId)}')` : 'void(0)'}" ${nextProtocolExperimentId ? '' : 'disabled'}>定位下一协议实验</button>
            <button class="secondary" type="button" onclick="${primaryBranch ? `previewRoadmapExperiment('${esc(primaryBranch.experimentId)}')` : 'void(0)'}" ${primaryBranch ? '' : 'disabled'}>定位关键支线</button>
          </div>
        </div>
      `;
    }

    function experimentLibraryDetailMeta(experimentId, config) {
      const mapping = {
        quick_pd_gain_sweep: {
          baseline: '先建立 quick_pd_zero 的比例增益基线，再决定后续验收门限。',
          next: '建议下一步接 quick_pd_damping_sweep，把阻尼和平滑性一并收紧。',
          platform: '这是最适合做平台首次闭环展示和控制器整定入门的实验。',
        },
        quick_pd_damping_sweep: {
          baseline: '建立在 quick_pd_gain_sweep 之后，用微分项继续压振荡。',
          next: '建议随后接 quick_pd_seed_mc，确认调好的参数在随机扰动下仍然稳定。',
          platform: '适合作为“从能收敛到收敛更干净”的第二层整定实验。',
        },
        quick_pd_seed_mc: {
          baseline: '在已整定好的 quick_pd_zero 基线上扩成轻量 Monte Carlo。',
          next: '建议随后接 quick_sensor_noise_sensitivity 或 cubesat_rw_fault_seed_mc，分别看测量退化和故障鲁棒性。',
          platform: '这是把单次漂亮结果升级成统计结论的第一步。',
        },
        quick_controller_benchmark_compare: {
          baseline: '在统一 quick_controller_benchmark 场景下固定环境和任务，只比较控制器差异。',
          next: '建议随后把表现较好的控制器接到环境或故障场景，继续做工程验证。',
          platform: '它是平台后续扩控制器类型时最重要的统一比较入口。',
        },
        quick_controller_benchmark_orbital_compare: {
          baseline: '把控制器比较从零扰动基线推进到轻量轨道环境，观察排序是否仍稳定。',
          next: '建议随后把较优控制器接到扰动分解或故障场景，继续看环境和执行器约束下的表现。',
          platform: '它让控制器 benchmark 不只停留在理想环境，更接近工程化比较入口。',
        },
        quick_environment_compare: {
          baseline: '用同一 quick_pd_zero 闭环直接比较 zero 与 orbital 两类环境。',
          next: '建议随后接 cubesat_rw_disturbance_breakdown，把环境差异继续拆到具体扰动项。',
          platform: '这是从理想闭环演示走向工程环境判断的关键过渡实验。',
        },
        quick_pd_acceptance_gate: {
          baseline: '在已能收敛的 quick_pd_zero 基线上，用更严格门限重看哪些参数点真正稳健。',
          next: '建议随后接 quick_pd_seed_mc，检查严格验收下通过的参数在随机扰动中是否仍然可靠。',
          platform: '它把平台从“能跑出来”推进到“结果判读是否足够严谨”。',
        },
        cubesat_rw_disturbance_breakdown: {
          baseline: '以轨道环境基线为前提，逐项打开重力梯度、残余磁矩、气动和太阳压。',
          next: '建议随后接 cubesat_rw_disturbance_capability_tradeoff 或执行器边界实验，把扰动强度和执行器余量联系起来。',
          platform: '它直接对应成熟平台里的环境扰动预算分析入口。',
        },
        cubesat_rw_disturbance_capability_tradeoff: {
          baseline: '在完成扰动分解后，再把主导扰动与执行器力矩边界放到同一张实验表里比较。',
          next: '建议随后延长时长或引入更细的轮组模型，观察长期轮速管理和能力裕度。',
          platform: '它把 environment setup 与 actuator boundary 两条主线真正耦合起来了。',
        },
        quick_sensor_noise_sensitivity: {
          baseline: '保持控制器和环境不变，只放大陀螺噪声。',
          next: '建议随后接 Monte Carlo 或控制器 benchmark，判断控制律对测量退化的敏感程度。',
          platform: '它让结果从“系统能控”继续推进到“感知链够不够好”。',
        },
        cubesat_rw_fault_seed_mc: {
          baseline: '在故障与轨道环境同时存在的场景里统计最差工况。',
          next: '建议随后接 cubesat_rw_fault_gain_tradeoff，尝试做故障后重整定。',
          platform: '它是故障场景从单次演示走向统计鲁棒性的核心实验。',
        },
        cubesat_rw_sun_transition_curated: {
          baseline: '把 detumble -> sun_pointing 固化成可回放、可解释的任务模式切换流程。',
          next: '建议随后接对地模式或更复杂 runtime，继续扩任务时间线与回放。',
          platform: '它最适合展示平台的 mission timeline、回放和 runtime 联动能力。',
        },
        cubesat_rw_earth_transition_curated: {
          baseline: '把 detumble -> earth_pointing 固化成可回放、可解释的对地任务流程，并显式使用 nadir 参考。',
          next: '建议随后接轮速管理或执行器能力实验，继续看对地任务在执行器边界附近是否仍稳定。',
          platform: '它让任务实验从太阳指向进一步扩展到更工程化的对地任务表达。',
        },
        cubesat_rw_earth_transition_wheel_capability: {
          baseline: '先固定对地任务流程，再只扫描轮组最大力矩，观察任务切换与执行器边界是如何耦合的。',
          next: '建议随后接轮速管理实验，继续看对地任务在长期运行下的执行器余量是否仍够用。',
          platform: '它把 mission 和 actuator 两条主线直接串起来，更像真实平台里的任务可行性验证实验。',
        },
        cubesat_rw_fault_gain_tradeoff: {
          baseline: '以故障姿态保持为目标，只扫描比例增益，观察误差改善与力矩边界冲突。',
          next: '建议随后接 cubesat_rw_wheel_capability，看是参数问题还是执行器能力问题。',
          platform: '它帮助把“故障后不稳”分解成“参数不合适”还是“执行器不够”。',
        },
        cubesat_rw_wheel_capability: {
          baseline: '在相同故障与环境下只改变轮组力矩能力。',
          next: '建议随后接 cubesat_rw_momentum_management_sweep，继续看轮速回拉和长期余量。',
          platform: '它是执行器能力边界分析的标准入口。',
        },
        cubesat_rw_momentum_management_sweep: {
          baseline: '固定任务和环境，只比较动量管理增益的不同策略。',
          next: '建议随后扩展更长时长、更多轨道圈和后续高保真轮组模型。',
          platform: '它开始把平台从短时姿态误差比较推进到执行机构长期可持续运行。',
        },
      };
      return mapping[experimentId] || {
        baseline: config?.description || '当前实验用于形成可复现实验模板。',
        next: '建议继续沿同类变量做更深一层实验拆解。',
        platform: '当前实验适合沉淀为平台可复用资产。',
      };
    }

    function experimentAcademicMeta(experimentId, config) {
      const themeDefaults = {
        '控制器整定': {
          hypothesis: '适度调整控制增益可以改善收敛速度或振荡抑制，但同时会改变控制动作强度与稳定裕度。',
          prerequisites: '保持同一场景、环境、参考目标和验收模板，只改变控制器参数，避免混入其他变量影响。',
          plots: '末端误差、RMS 误差、峰值力矩、姿态误差时序、控制力矩时序。',
          takeaway: '先找到可工作的参数带，再决定是否继续做阻尼整定、鲁棒性检查或故障后重整定。',
        },
        '鲁棒性': {
          hypothesis: '同一控制配置在随机噪声、初值和种子变化下会出现可统计的最差工况边界。',
          prerequisites: '先固定一套已经基本可用的控制器与任务流程，再放大随机因素做统计比较。',
          plots: '通过率、最佳-最差差距、误差分布、run 排行、最差 run 时序。',
          takeaway: '把单次成功结果升级成统计结论，识别是否存在边界工况与脆弱参数区。',
        },
        '控制器 benchmark': {
          hypothesis: '在统一场景和任务下，不同控制律会表现出不同的误差收敛、控制动作和鲁棒性特征。',
          prerequisites: '统一环境、初始状态、参考目标和验收门限，确保变化只来自控制器本身。',
          plots: '控制器对比表、误差曲线对比、控制力矩对比、最佳/最差 run 摘要。',
          takeaway: '形成平台默认控制器基线，并给后续扩控制律留下统一比较入口。',
        },
        '验收门限': {
          hypothesis: '更严格的验收规则会优先筛掉边界参数点，从而暴露哪些结果只是勉强可用，哪些结果真正稳健。',
          prerequisites: '先固定同一场景、同一变量和同一任务，再收紧验收标准，避免把动力学差异和评估口径混在一起。',
          plots: '通过率变化、边界 run 排行、末端误差/RMS 误差对比、严格门限下的 accepted-failed 划分。',
          takeaway: '帮助平台把“通过”定义收敛得更严谨，也让后续报告和回归验收更可信。',
        },
        '环境敏感性': {
          hypothesis: '从理想环境切到轨道环境后，姿态误差、控制力矩和扰动力矩预算会首先在一部分指标上显著变化。',
          prerequisites: '固定控制器和任务，仅改变环境建模层，避免把控制参数差异误判为环境效应。',
          plots: '环境对比表、扰动力矩峰值、误差曲线、执行力矩曲线、通过率摘要。',
          takeaway: '回答环境是否重要，并决定是否要继续进入扰动分解和更高保真环境建模。',
        },
        '扰动分解': {
          hypothesis: '在当前轨道、姿态和结构下，会存在一类主导扰动项，对误差和扰动力矩预算贡献最显著。',
          prerequisites: '先确认 orbital 环境整体有影响，再逐项拆解主要扰动模板，避免在 zero 环境里做无效比较。',
          plots: '主导扰动项、扰动力矩预算、误差退化顺序、不同扰动模板的 run 排行。',
          takeaway: '帮助决定哪类外部扰动最值得优先精细建模和做工程约束。',
        },
        '测量敏感性': {
          hypothesis: '测量链精度下降会先在估计误差、闭环误差或通过率上暴露出来。',
          prerequisites: '保持控制器、环境和任务不变，仅调整传感器噪声或测量质量参数。',
          plots: '噪声档位对比、误差曲线、通过率、最差 run 摘要。',
          takeaway: '把鲁棒性进一步细化为感知链边界判断，明确传感器质量约束。',
        },
        '任务模式切换': {
          hypothesis: '模式切换过程中的误差峰值、控制力矩波动与时间线一致性，是比稳态误差更关键的任务指标。',
          prerequisites: '先定义清楚 mission 模板、模式区间和参考切换，再观察姿态回放与 mode timeline。',
          plots: '模式时间线、姿态回放、切换段误差峰值、控制力矩时序、runtime 调度摘要。',
          takeaway: '验证任务流程是否可解释、切换是否平滑，并给更复杂 mission sequence 打底。',
        },
        '执行器边界': {
          hypothesis: '当执行器能力降低或外部扰动增强时，系统会先在饱和、峰值力矩或末端误差上暴露瓶颈。',
          prerequisites: '优先固定任务和环境，再独立或联合扫描执行器能力参数，避免边界来源混淆。',
          plots: '峰值力矩、通过率、轮速趋势、饱和标志、最优/最差 run 对比。',
          takeaway: '帮助判断是参数需要重整定，还是执行机构能力本身已经不够。',
        },
        '轮速管理': {
          hypothesis: '更积极的轮速管理会改善长期执行器余量，但可能开始干扰主姿态任务性能。',
          prerequisites: '使用带反作用轮状态的场景，并保持主任务、环境和控制器基本不变。',
          plots: '轮速趋势、动量利用、姿态误差、执行器余量、峰值力矩。',
          takeaway: '把平台从短时闭环误差比较推进到长期执行机构运行策略分析。',
        },
      };
      const overrides = {
        cubesat_rw_disturbance_capability_tradeoff: {
          hypothesis: '主导扰动增强与轮组最大力矩下降会共同决定姿态保持能力边界，且二者并非简单线性替代。',
          prerequisites: '先完成环境扰动分解，并使用带反作用轮的轨道场景，再做二维扫描。',
          plots: '二维权衡表、主导扰动项、通过率热区、最佳/最差 run 对比。',
          takeaway: '把 environment setup 与 actuator boundary 两条主线耦合起来，识别真正的工程瓶颈。',
        },
        cubesat_rw_sun_transition_curated: {
          hypothesis: '如果任务模式切换设计合理，那么 detumble 到 sun_pointing 的过渡将表现为可解释、可回放、可通过验收的时间线。',
          prerequisites: '需要明确模式步骤、切换持续时间和参考目标，并保留完整时序与回放数据。',
          plots: 'mode timeline、姿态回放、切换段误差峰值、控制力矩和 runtime 快照。',
          takeaway: '最适合展示平台在 mission、runtime、回放和结果报告上的整体联动能力。',
        },
        cubesat_rw_earth_transition_curated: {
          hypothesis: '如果对地任务定义合理，那么 detumble 到 earth_pointing 的过渡应表现为可解释、可回放，且与 nadir 参考一致的时间线。',
          prerequisites: '需要明确 detumble 时长、earth_pointing 模式和 nadir 参考，并保留完整时序、timeline 与回放数据。',
          plots: 'mode timeline、姿态回放、切换段误差峰值、控制力矩和对地任务摘要。',
          takeaway: '它把 mission 资产从太阳指向扩到对地任务，更接近真实平台里的任务族谱。',
        },
        cubesat_rw_earth_transition_wheel_capability: {
          hypothesis: '对地任务在执行器能力下降时，会比单纯稳态保持更早暴露过渡峰值、力矩边界或控制余量瓶颈。',
          prerequisites: '先固定对地任务流程、nadir 参考和 detumble 时长，再独立扫描轮组最大力矩，避免把任务定义和执行器边界混在一起。',
          plots: 'transition peak、峰值力矩、mode timeline、姿态回放、最佳/最差 run 对比。',
          takeaway: '它把任务实验推进到任务可行性验证，适合回答“对地任务能不能在当前执行器条件下稳定完成”。',
        },
      };
      return overrides[experimentId] || themeDefaults[config?.theme || ''] || {
        hypothesis: '当前实验用于验证某一类配置变化是否会显著改变姿态控制结果。',
        prerequisites: '保持除目标变量外的设置尽量一致，确保结果具有可比较性。',
        plots: 'run 摘要表、关键误差指标、控制力矩与代表性时序图。',
        takeaway: '适合沉淀为平台标准实验模板，并继续沿实验主线扩展。',
      };
    }

    function experimentStandardCardMeta(experimentId, config, academicMeta) {
      const themeDefaults = {
        '控制器整定': {
          acceptance: '优先同时看末端误差、RMS 误差和峰值力矩，先筛出稳定参数带，再推进阻尼或鲁棒性验证。',
          figurePackage: academicMeta?.plots || '误差指标表、姿态误差时序、控制力矩时序。',
          derivedPlan: `${config?.planName || experimentId}_refine`,
          maturity: '入口基线 / 必做',
          assetRole: '协议入口实验，用来固定第一轮闭环整定边界。',
        },
        '鲁棒性': {
          acceptance: '重点看通过率、最差 run 与最佳 run 差距，以及是否存在明显边界工况。',
          figurePackage: academicMeta?.plots || '通过率摘要、run 排行、最差 run 时序。',
          derivedPlan: `${config?.planName || experimentId}_mc_followup`,
          maturity: '统计验证 / 推荐',
          assetRole: '把单次成功结果升级成统计意义上的稳定性判断。',
        },
        '控制器 benchmark': {
          acceptance: '统一场景和门限后比较末端误差、峰值力矩和 acceptance_rate，避免只看一张误差曲线。',
          figurePackage: academicMeta?.plots || '控制器对比表、误差对比、力矩对比。',
          derivedPlan: `${config?.planName || experimentId}_orbital_followup`,
          maturity: '标准比较入口',
          assetRole: '统一控制器比较入口，用来固化平台默认基线。',
        },
        '验收门限': {
          acceptance: '以 accepted / failed 边界为主，不再只看均值，适合把平台口径收紧成可回归规则。',
          figurePackage: academicMeta?.plots || '通过失败分界、边界 run 排行、严格门限对比。',
          derivedPlan: `${config?.planName || experimentId}_strict_gate_review`,
          maturity: '评估收口 / 推荐',
          assetRole: '把实验从“能跑”推进到“可验收、可复查”。',
        },
        '环境敏感性': {
          acceptance: '优先确认环境切换是否足以改变结论，再决定是否进入扰动分解或更高保真建模。',
          figurePackage: academicMeta?.plots || '环境对比表、扰动力矩预算、误差曲线。',
          derivedPlan: `${config?.planName || experimentId}_disturbance_followup`,
          maturity: '工程判断入口',
          assetRole: '环境 setup 入口实验，用来决定环境建模是否值得继续加深。',
        },
        '扰动分解': {
          acceptance: '需要能指出主导扰动项，并说明它对误差或扰动力矩预算的影响顺序。',
          figurePackage: academicMeta?.plots || '主导扰动项、预算曲线、run 排行。',
          derivedPlan: `${config?.planName || experimentId}_capability_tradeoff`,
          maturity: '预算分析 / 推荐',
          assetRole: '把环境影响拆成可解释的扰动来源，是高保真建模前的重要中间层。',
        },
        '测量敏感性': {
          acceptance: '优先看通过率和最差工况是否对测量质量退化敏感，再决定是否需要更稳健的控制或估计。',
          figurePackage: academicMeta?.plots || '噪声档位对比、误差曲线、最差 run 摘要。',
          derivedPlan: `${config?.planName || experimentId}_sensor_followup`,
          maturity: '感知链筛查',
          assetRole: '感知链入口实验，用来评估测量质量是否成为主约束。',
        },
        '任务模式切换': {
          acceptance: '重点看切换段误差峰值、力矩波动、timeline 一致性和回放可解释性，而不是只看末端误差。',
          figurePackage: academicMeta?.plots || 'mode timeline、姿态回放、切换峰值、runtime 快照。',
          derivedPlan: `${config?.planName || experimentId}_mission_followup`,
          maturity: '任务演示 / 核心展示',
          assetRole: '任务序列入口实验，用来验证 mission、runtime 与 replay 是否形成闭环。',
        },
        '执行器边界': {
          acceptance: '优先判断瓶颈先落在峰值力矩、饱和还是末端误差，再决定是重整定还是换能力档位。',
          figurePackage: academicMeta?.plots || '峰值力矩、轮速趋势、饱和标志、最佳/最差对比。',
          derivedPlan: `${config?.planName || experimentId}_margin_followup`,
          maturity: '边界验证 / 核心展示',
          assetRole: '执行机构边界实验，用来回答“当前执行器够不够”。',
        },
        '轮速管理': {
          acceptance: '兼顾姿态误差与轮速回拉趋势，避免只因为余量改善就忽略主任务性能退化。',
          figurePackage: academicMeta?.plots || '轮速趋势、动量利用、姿态误差、执行器余量。',
          derivedPlan: `${config?.planName || experimentId}_long_horizon`,
          maturity: '长期运行扩展',
          assetRole: '把平台从短时姿态误差比较推进到长期执行器策略分析。',
        },
      };
      const overrides = {
        quick_pd_gain_sweep: {
          derivedPlan: 'quick_pd_damping_sweep 或 quick_pd_gain_sweep_refine',
          maturity: '闭环入口 / 必做',
        },
        quick_pd_damping_sweep: {
          derivedPlan: 'quick_pd_seed_mc',
          maturity: '基线整定 / 必做',
        },
        quick_pd_seed_mc: {
          derivedPlan: 'quick_sensor_noise_sensitivity 或 cubesat_rw_fault_seed_mc',
        },
        quick_controller_benchmark_compare: {
          derivedPlan: 'quick_controller_benchmark_orbital_compare',
        },
        quick_environment_compare: {
          derivedPlan: 'cubesat_rw_disturbance_breakdown',
        },
        cubesat_rw_disturbance_breakdown: {
          derivedPlan: 'cubesat_rw_disturbance_capability_tradeoff',
        },
        cubesat_rw_sun_transition_curated: {
          derivedPlan: 'cubesat_rw_earth_transition_curated',
        },
        cubesat_rw_earth_transition_curated: {
          derivedPlan: 'cubesat_rw_earth_transition_wheel_capability',
        },
        cubesat_rw_earth_transition_wheel_capability: {
          derivedPlan: 'cubesat_rw_momentum_management_sweep',
        },
        cubesat_rw_fault_gain_tradeoff: {
          derivedPlan: 'cubesat_rw_wheel_capability',
        },
        cubesat_rw_wheel_capability: {
          derivedPlan: 'cubesat_rw_momentum_management_sweep',
        },
      };
      return {
        acceptance: '当前实验需要有清楚的误差、力矩和通过标准。',
        figurePackage: academicMeta?.plots || '关键指标表、代表性时序图和结果摘要。',
        derivedPlan: `${config?.planName || experimentId}_followup`,
        maturity: '可复用模板',
        assetRole: '适合沉淀成平台标准实验资产。',
        ...(themeDefaults[config?.theme || ''] || {}),
        ...(overrides[experimentId] || {}),
      };
    }

    function referenceExplanation(reference) {
      const mapping = {
        body_zero: '机体系零姿态参考，适合最小闭环保持和参数整定基线。',
        sun: '太阳参考，适合太阳指向和模式切换展示。',
        nadir: '对地 / nadir 参考，适合对地任务和载荷指向场景。',
      };
      return mapping[reference] || '当前参考定义了控制器要跟踪的目标姿态。';
    }

    function scenarioBaselineSummary(scenarioPath, fallback = '') {
      const scenario = [...(state.workspace?.scenarios || [])].find(item =>
        item.path === scenarioPath || item.name === scenarioPath
      ) || null;
      if (scenario) {
        const summary = [
          scenario.name || scenarioPath,
          `${fmt(scenario.duration_s)} s / dt ${fmt(scenario.dt_s)} s`,
          `${scenario.system || '—'} / ${scenario.controller || '—'}`,
          scenario.environment || '—',
        ].filter(Boolean).join(' · ');
        return `${summary}。${scenario.description || fallback || '当前实验基于该场景展开。'}`;
      }
      if (scenarioPath) {
        return `${scenarioPath}。${fallback || '当前实验基于这个场景基线展开。'}`;
      }
      return fallback || '当前实验基于一组固定动力学、控制器和环境设置展开。';
    }

    function missionTemplateSummary(options = {}) {
      const mission = options.mission || 'single_mode';
      const mode = options.mode || 'inertial_hold';
      const reference = options.reference || 'body_zero';
      const detumbleS = Number(options.detumbleS);
      if (mission === 'detumble_then_hold') {
        const detumbleText = Number.isFinite(detumbleS) && detumbleS > 0
          ? `先执行 detumble ${fmt(detumbleS)} s`
          : '先执行 detumble';
        return `消旋后保持：${detumbleText}，再进入 ${mode}。${modeExplanation(mode)} 参考目标为 ${reference}，${referenceExplanation(reference)}`;
      }
      return `单模式保持：全程维持 ${mode}。${modeExplanation(mode)} 参考目标为 ${reference}，${referenceExplanation(reference)}`;
    }

    function experimentVariableSummary(options = {}) {
      if (options.variableSummary) return String(options.variableSummary);
      const sweepRows = Array.isArray(options.sweepRows) ? options.sweepRows : [];
      const mcSamples = Number(options.mcSamples || 0);
      const sweepPath = String(options.sweepPath || '').trim();
      const secondSweepPath = String(options.secondSweepPath || '').trim();
      const parts = [];
      if (sweepPath) {
        parts.push(inferSweepMeaning(sweepPath));
      } else if (options.variableLabel) {
        parts.push(`当前实验主要围绕 ${options.variableLabel} 展开。`);
      } else {
        parts.push('当前实验默认按单场景单配置运行。');
      }
      if (sweepRows.length) {
        parts.push(`当前取值组织为 ${sweepRows.map(row => `${row.label} ${row.count} 档`).join('；')}。`);
      }
      if (secondSweepPath) {
        parts.push(inferSweepMeaning(secondSweepPath));
      }
      if (mcSamples > 0) {
        parts.push(`同时启用 Monte Carlo ${mcSamples} 次，用于补统计结论和最差工况边界。`);
      }
      return parts.join(' ');
    }

    function experimentAcceptanceSummary(options = {}) {
      if (options.acceptanceSummary) return String(options.acceptanceSummary);
      const rows = Array.isArray(options.acceptanceRows) ? options.acceptanceRows : [];
      const acceptanceConfig = options.acceptanceConfig || null;
      const rowText = rows.length ? rows.map(row => `${row.label} ${row.value}`).join('；') : '';
      if (rowText && acceptanceConfig?.help) {
        return `${acceptanceConfig.label}：${rowText}。${acceptanceConfig.help}`;
      }
      if (rowText) return rowText;
      if (acceptanceConfig?.help) return `${acceptanceConfig.label}：${acceptanceConfig.help}`;
      if (options.standardMeta?.acceptance) return options.standardMeta.acceptance;
      return '当前实验需要明确末端误差、RMS 误差和峰值力矩等通过标准。';
    }

    function experimentRecommendedFiguresSummary(options = {}) {
      if (options.figuresSummary) return String(options.figuresSummary);
      const standardFigures = options.standardMeta?.figurePackage || '';
      const academicFigures = options.academicMeta?.plots || '';
      if (standardFigures && academicFigures && standardFigures !== academicFigures) {
        return `${standardFigures} 优先；细读时再补 ${academicFigures}。`;
      }
      return standardFigures || academicFigures || '优先看关键指标表、姿态误差曲线、控制力矩曲线和代表 run 摘要。';
    }

    function experimentNextSummary(options = {}) {
      if (options.nextSummary) return String(options.nextSummary);
      if (options.detailMeta?.next) return options.detailMeta.next;
      if (options.standardMeta?.derivedPlan) return `建议继续派生 ${options.standardMeta.derivedPlan}。`;
      return '建议继续沿同类变量、任务或执行器边界扩展下一组实验。';
    }

    function experimentBriefMeta(options = {}) {
      const experimentId = options.experimentId || '';
      const config = options.config || (experimentId ? curatedExperimentConfig(experimentId) : null);
      const detailMeta = options.detailMeta || experimentLibraryDetailMeta(experimentId, config);
      const academicMeta = options.academicMeta || experimentAcademicMeta(experimentId, config || {theme: ''});
      const standardMeta = options.standardMeta || experimentStandardCardMeta(
        experimentId,
        config || {planName: experimentId || 'experiment', theme: options.theme || ''},
        academicMeta,
      );
      const scenarioPath = options.scenarioPath || resolveCuratedExperimentScenario(config);
      const templateConfig = options.templateConfig || (config?.templateId ? experimentTemplateConfig(config.templateId) : null);
      const mission = options.mission || templateConfig?.mission || 'single_mode';
      const mode = options.mode || templateConfig?.mode || 'inertial_hold';
      const reference = options.reference || templateConfig?.reference || 'body_zero';
      const detumbleS = options.detumbleS ?? templateConfig?.detumbleS ?? 0;
      const theme = options.theme || config?.theme || '';
      return {
        theme,
        question: options.question || config?.question || config?.description || '当前实验用于回答某一类姿态控制配置变化是否会显著影响结果。',
        scenarioBaseline: scenarioBaselineSummary(scenarioPath, detailMeta?.baseline || config?.description || ''),
        variableDesign: experimentVariableSummary({
          ...options,
          variableLabel: options.variableLabel || config?.variable || templateConfig?.label || '',
        }),
        taskTemplate: options.taskSummary || missionTemplateSummary({mission, mode, reference, detumbleS}),
        acceptanceGate: experimentAcceptanceSummary({...options, standardMeta}),
        recommendedFigures: experimentRecommendedFiguresSummary({...options, standardMeta, academicMeta}),
        nextExperiment: experimentNextSummary({...options, detailMeta, standardMeta}),
        badges: [
          theme ? `主题 ${theme}` : '',
          scenarioPath ? `场景 ${scenarioPath}` : '',
          config?.planName ? `计划 ${config.planName}` : '',
          templateConfig?.label ? `模板 ${templateConfig.label}` : '',
        ].filter(Boolean),
      };
    }

    function experimentBriefHtml(brief, options = {}) {
      if (!brief) return '';
      const lead = options.lead || '固定回答研究问题、场景基线、变量、任务、验收、结果图和下一步，避免页面各说各话。';
      return `
        <div class="history-detail" data-legacy-title="实验任务书" style="margin-top:${options.compact ? '10px' : '12px'}; margin-bottom:0">
          <strong>${esc(options.title || '实验任务说明卡')}</strong>
          <p>${esc(lead)}</p>
          ${brief.badges?.length ? `<div class="chips" style="margin-top:8px">${brief.badges.map(text => `<span class="chip">${esc(text)}</span>`).join('')}</div>` : ''}
          <div class="detail-grid" style="margin-top:10px">
            <div class="detail-box"><strong>1. 研究问题</strong><div>${esc(brief.question)}</div></div>
            <div class="detail-box"><strong>2. 场景基线</strong><div>${esc(brief.scenarioBaseline)}</div></div>
            <div class="detail-box"><strong>3. 扫描变量</strong><div>${esc(brief.variableDesign)}</div></div>
            <div class="detail-box"><strong>4. 任务模板</strong><div>${esc(brief.taskTemplate)}</div></div>
            <div class="detail-box"><strong>5. 验收口径</strong><div>${esc(brief.acceptanceGate)}</div></div>
            <div class="detail-box"><strong>6. 推荐结果图</strong><div>${esc(brief.recommendedFigures)}</div></div>
            <div class="detail-box"><strong>7. 下一步实验</strong><div>${esc(brief.nextExperiment)}</div></div>
          </div>
        </div>
      `;
    }

    function experimentReadingTaskMeta(options = {}) {
      const experimentId = options.experimentId || '';
      const config = options.config || (experimentId ? curatedExperimentConfig(experimentId) : null);
      const detailMeta = options.detailMeta || experimentLibraryDetailMeta(experimentId, config);
      const academicMeta = options.academicMeta || experimentAcademicMeta(experimentId, config || {theme: ''});
      const standardMeta = options.standardMeta || experimentStandardCardMeta(
        experimentId,
        config || {planName: experimentId || 'experiment', theme: options.theme || ''},
        academicMeta,
      );
      const theme = options.theme || config?.theme || '';
      const nextExperiment = experimentNextSummary({...options, detailMeta, standardMeta});
      const defaults = {
        '控制器整定': {
          firstRead: '先看 summary_metrics 和最佳 / 较差 run，判断误差改善是不是靠更大控制动作换来的。',
          secondRead: '再去结果对比看误差和力矩曲线，确认收敛速度、振荡和峰值力矩的平衡点。',
          reportFocus: '报告中优先写“哪一段参数带既能压误差，又没有明显放大控制动作”。',
        },
        '鲁棒性': {
          firstRead: '先看 acceptance_rate、最差 run 和 run 排行，判断边界工况是否已经暴露。',
          secondRead: '再把最差 run 送到对比或回放，确认退化来自噪声、初值还是故障场景本身。',
          reportFocus: '报告中优先写“统计上最差工况有多差，以及当前参数是否仍有稳定余量”。',
        },
        '控制器 benchmark': {
          firstRead: '先看控制器对比表和最佳 run，确认 PD 与 LADRC 在统一条件下的排序。',
          secondRead: '再看误差与力矩曲线，避免只因为一条误差曲线更低就忽略控制代价差异。',
          reportFocus: '报告中优先写“平台默认控制器基线为什么选它，以及代价是什么”。',
        },
        '验收门限': {
          firstRead: '先看 accepted / failed 边界和通过率变化，确认门限是否真的把边界参数点筛出来。',
          secondRead: '再比较边界 run 的误差和力矩，确认失败来自末端、RMS 还是执行器门限。',
          reportFocus: '报告中优先写“平台通过标准收紧后，哪些结果还算稳健”。',
        },
        '环境敏感性': {
          firstRead: '先看环境切换后的摘要卡和扰动力矩预算，确认环境层是否足以改变当前结论。',
          secondRead: '再看误差 / 力矩曲线，区分是环境本身重要，还是控制器对环境过于敏感。',
          reportFocus: '报告中优先写“zero 到 orbital 切换后，最先变化的是哪组指标”。',
        },
        '扰动分解': {
          firstRead: '先看主导扰动项和 budget 摘要，确认哪一类外部力矩最值得优先解释。',
          secondRead: '再看不同扰动模板的 run 排行和误差曲线，区分主导项和次级项。',
          reportFocus: '报告中优先写“哪类扰动主导、它如何影响误差和执行器余量”。',
        },
        '测量敏感性': {
          firstRead: '先看不同噪声档位下的通过率和最差 run，判断感知链是不是先成为瓶颈。',
          secondRead: '再看误差曲线，确认退化是稳态偏差、振荡增大还是收敛变慢。',
          reportFocus: '报告中优先写“当前传感器质量还能接受到什么程度”。',
        },
        '任务模式切换': {
          firstRead: '先看结果总览里的切换段摘要和 mode timeline，确认任务过程有没有明显断层。',
          secondRead: '再去姿态回放与 runtime 快照，检查切换峰值、参考目标和时间线是否一致。',
          reportFocus: '报告中优先写“任务切换是否平滑、可解释，以及峰值出现在什么阶段”。',
        },
        '执行器边界': {
          firstRead: '先看失败结果、峰值力矩和轮速趋势，确认瓶颈先落在误差、饱和还是执行器余量。',
          secondRead: '再做结果对比或回放，判断是任务流程太激进，还是轮组能力档位确实不够。',
          reportFocus: '报告中优先写“当前执行器边界在哪里，换参数是否足够还是必须换能力”。',
        },
        '轮速管理': {
          firstRead: '先看轮速趋势和姿态误差，确认余量改善是否伴随主任务性能退化。',
          secondRead: '再看长期 run 或最佳 / 最差配置对比，区分短时改善和长期副作用。',
          reportFocus: '报告中优先写“轮速管理增益如何影响执行器长期可持续运行”。',
        },
      };
      const meta = defaults[theme] || {
        firstRead: '先看结果摘要和关键指标，确认当前实验回答的问题有没有被清楚地展开。',
        secondRead: '再看图表、对比或回放，确认关键差异来自变量、环境还是任务流程。',
        reportFocus: '报告中优先写“当前实验的主要判断，以及它如何接到下一组实验”。',
      };
      return {
        firstRead: options.firstRead || meta.firstRead,
        secondRead: options.secondRead || meta.secondRead,
        reportFocus: options.reportFocus || meta.reportFocus,
        nextAction: options.nextAction || nextExperiment,
        deliverables: options.deliverables || config?.outputs || standardMeta.figurePackage,
      };
    }

    function experimentReadingTaskHtml(task, options = {}) {
      if (!task) return '';
      return `
        <div class="history-detail" style="margin-top:${options.compact ? '10px' : '12px'}; margin-bottom:0">
          <strong>${esc(options.title || '结果阅读任务书')}</strong>
          <p>${esc(options.lead || '不只说明实验要做什么，也说明跑完后先看什么、怎么写结论、下一步往哪里接。')}</p>
          <div class="flow-sequence" style="margin-top:10px">
            <div class="flow-node accent">
              <small>1. 先看什么</small>
              <strong>结果首屏</strong>
              <p>${esc(task.firstRead)}</p>
            </div>
            <div class="flow-node">
              <small>2. 再看什么</small>
              <strong>对比 / 回放 / 图表</strong>
              <p>${esc(task.secondRead)}</p>
            </div>
            <div class="flow-node warm">
              <small>3. 结论怎么写</small>
              <strong>报告焦点</strong>
              <p>${esc(task.reportFocus)}</p>
            </div>
            <div class="flow-node ok">
              <small>4. 接到哪里</small>
              <strong>下一步实验</strong>
              <p>${esc(task.nextAction)}</p>
            </div>
          </div>
          <div class="detail-grid" style="margin-top:10px">
            <div class="detail-box"><strong>推荐交付物</strong><div>${esc(task.deliverables)}</div></div>
            <div class="detail-box"><strong>下一步动作</strong><div>${esc(task.nextAction)}</div></div>
          </div>
        </div>
      `;
    }

    function resolveQuickDemoScenario(config) {
      return (config?.scenarioPaths || []).find(path => state.workspace?.scenarios?.some(item => item.path === path)) || '';
    }

    function resolveQuickDemoPlan(config) {
      const planName = normalizedAssetName(config?.planName || '');
      const directPath = config?.planName ? `scenarios/${config.planName}.json` : '';
      if (directPath && state.workspace?.experiments?.some(item => item.path === directPath)) {
        return directPath;
      }
      if (!planName) return '';
      const matched = (state.workspace?.experiments || []).find(item => normalizedAssetName(item.path || item.name || '') === planName);
      return matched?.path || '';
    }

    function quickDemoPlanRecord(config) {
      const path = resolveQuickDemoPlan(config);
      return (state.workspace?.experiments || []).find(item => item.path === path) || null;
    }

    function resolveCuratedExperimentScenario(config) {
      return (config?.scenarioPaths || []).find(path => state.workspace?.scenarios?.some(item => item.path === path)) || '';
    }

    function resolveCuratedExperimentPlan(config) {
      return (config?.planPaths || []).find(path => state.workspace?.experiments?.some(item => item.path === path)) || '';
    }

    function findCuratedExperimentIdByPlanPath(planPath) {
      if (!planPath) return '';
      return experimentLibraryIds().find(id => {
        const config = curatedExperimentConfig(id);
        return resolveCuratedExperimentPlan(config) === planPath;
      }) || '';
    }

    function findCuratedExperimentIdForResult(result) {
      const matchedPlan = [...(state.workspace?.experiments || [])].find(plan => matchingDashboardForPlan(plan)?.path === result?.path);
      const byPlan = findCuratedExperimentIdByPlanPath(matchedPlan?.path || '');
      if (byPlan) return byPlan;
      const name = normalizedAssetName(result?.experiment_name || result?.name || '');
      const folder = dashboardFolderName(result);
      return experimentLibraryIds().find(id => {
        const config = curatedExperimentConfig(id);
        const planName = normalizedAssetName(config?.planName || '');
        return Boolean(planName) && (planName === name || planName === folder);
      }) || '';
    }

    function experimentRoadmapProfile(currentId, resultTheme) {
      if (currentId === 'quick_pd_gain_sweep' || currentId === 'quick_pd_damping_sweep' || currentId === 'quick_pd_seed_mc') {
        return {
          label: '控制器整定与鲁棒性链路',
          description: '先建立控制器基线，再收紧阻尼与鲁棒性，形成稳定可复用的闭环基线。',
          ids: ['quick_pd_gain_sweep', 'quick_pd_damping_sweep', 'quick_pd_seed_mc'],
        };
      }
      if (currentId === 'quick_controller_benchmark_compare') {
        return {
          label: '控制器比较链路',
          description: '先比较控制器，再把较优控制器带入整定和更真实场景。',
          ids: ['quick_controller_benchmark_compare', 'quick_controller_benchmark_orbital_compare', 'quick_pd_seed_mc'],
        };
      }
      if (currentId === 'quick_pd_acceptance_gate') {
        return {
          label: '整定到验收链路',
          description: '先找到可用参数，再用更严格验收重看边界点，最后扩到随机鲁棒性。',
          ids: ['quick_pd_gain_sweep', 'quick_pd_damping_sweep', 'quick_pd_acceptance_gate', 'quick_pd_seed_mc'],
        };
      }
      if (currentId === 'quick_environment_compare' || currentId === 'cubesat_rw_disturbance_breakdown' || currentId === 'cubesat_rw_disturbance_capability_tradeoff' || currentId === 'cubesat_rw_wheel_capability' || currentId === 'cubesat_rw_earth_transition_wheel_capability' || currentId === 'cubesat_rw_momentum_management_sweep') {
        return {
          label: '环境到执行器权衡链路',
          description: '从环境是否重要，一路推进到主导扰动、执行器能力边界和轮速管理。',
          ids: ['quick_environment_compare', 'cubesat_rw_disturbance_breakdown', 'cubesat_rw_disturbance_capability_tradeoff', 'cubesat_rw_wheel_capability', 'cubesat_rw_earth_transition_wheel_capability', 'cubesat_rw_momentum_management_sweep'],
        };
      }
      if (currentId === 'quick_sensor_noise_sensitivity') {
        return {
          label: '测量质量链路',
          description: '先确定测量噪声边界，再把感知链结论带入更复杂环境或故障场景。',
          ids: ['quick_sensor_noise_sensitivity', 'quick_pd_seed_mc', 'cubesat_rw_fault_seed_mc'],
        };
      }
      if (currentId === 'cubesat_rw_fault_seed_mc' || currentId === 'cubesat_rw_fault_gain_tradeoff') {
        return {
          label: '故障鲁棒性链路',
          description: '先看故障统计边界，再做故障后重整定与执行器余量判断。',
          ids: ['cubesat_rw_fault_seed_mc', 'cubesat_rw_fault_gain_tradeoff', 'cubesat_rw_wheel_capability'],
        };
      }
      if (currentId === 'cubesat_rw_sun_transition_curated' || currentId === 'cubesat_rw_earth_transition_curated') {
        return {
          label: '任务模式切换链路',
          description: '围绕任务流程、模式切换、时间线和回放逐步展开。',
          ids: ['cubesat_rw_sun_transition_curated', 'cubesat_rw_earth_transition_curated', 'cubesat_rw_earth_transition_wheel_capability', 'cubesat_rw_momentum_management_sweep'],
        };
      }
      if (resultTheme === '环境敏感性' || resultTheme === '扰动分解') {
        return {
          label: '环境到执行器权衡链路',
          description: '从环境敏感性出发，逐步推进到扰动分解和执行器余量分析。',
          ids: ['quick_environment_compare', 'cubesat_rw_disturbance_breakdown', 'cubesat_rw_disturbance_capability_tradeoff'],
        };
      }
      if (resultTheme === '执行器边界' || resultTheme === '轮速管理') {
        return {
          label: '执行器边界链路',
          description: '围绕执行器能力、扰动耦合和轮速管理逐步收敛工程边界。',
          ids: ['cubesat_rw_disturbance_capability_tradeoff', 'cubesat_rw_wheel_capability', 'cubesat_rw_earth_transition_wheel_capability', 'cubesat_rw_momentum_management_sweep'],
        };
      }
      if (resultTheme === '控制器整定' || resultTheme === '鲁棒性') {
        return {
          label: '控制器整定与鲁棒性链路',
          description: '先找到可用参数，再扩展统计鲁棒性和更真实工况。',
          ids: ['quick_pd_gain_sweep', 'quick_pd_damping_sweep', 'quick_pd_seed_mc'],
        };
      }
      if (resultTheme === '验收门限') {
        return {
          label: '整定到验收链路',
          description: '先建立控制器基线，再收紧验收门限，最后扩展到随机鲁棒性。',
          ids: ['quick_pd_gain_sweep', 'quick_pd_damping_sweep', 'quick_pd_acceptance_gate', 'quick_pd_seed_mc'],
        };
      }
      if (resultTheme === '任务模式切换') {
        return {
          label: '任务模式切换链路',
          description: '围绕 detumble、目标模式、时间线和回放逐步展开任务实验资产。',
          ids: ['cubesat_rw_sun_transition_curated', 'cubesat_rw_earth_transition_curated', 'cubesat_rw_earth_transition_wheel_capability', 'cubesat_rw_momentum_management_sweep'],
        };
      }
      return {
        label: '平台实验主线',
        description: '把当前结果先沉淀成标准实验，再沿实验库继续向环境、任务或执行器边界扩展。',
        ids: ['quick_pd_gain_sweep', 'quick_environment_compare', 'cubesat_rw_sun_transition_curated', 'cubesat_rw_earth_transition_curated'],
      };
    }

    function openRoadmapExperiment(experimentId) {
      const config = curatedExperimentConfig(experimentId);
      if (!config) return;
      const planPath = resolveCuratedExperimentPlan(config);
      if (planPath) {
        showExperiment(planPath);
        return;
      }
      const scenarioPath = resolveCuratedExperimentScenario(config);
      if (scenarioPath) {
        prepareCuratedExperiment(experimentId);
      }
    }

    function previewRoadmapExperiment(experimentId) {
      const config = curatedExperimentConfig(experimentId);
      if (!config) return;
      state.libraryCategory = experimentLibraryCategoryId(config);
      state.libraryExperimentId = experimentId;
      state.libraryView = 'template';
      switchLabView('library');
    }

    async function prepareNextProtocolExperiment(protocolId, sourceLabel = '当前协议') {
      const protocol = experimentProtocolConfig(protocolId);
      const nextProtocolId = protocol?.nextProtocolId || '';
      const nextExperimentId = protocolPrimaryExperimentId(nextProtocolId, 'all');
      if (!nextProtocolId || !nextExperimentId) {
        setStatus(`${sourceLabel}当前没有可直接载入的下一协议模板。`, 'bad');
        return null;
      }
      const nextProtocol = experimentProtocolConfig(nextProtocolId);
      const result = await prepareCuratedExperiment(nextExperimentId);
      if (!result) return null;
      pushActivity(
        '已切到下一协议模板',
        `${protocol?.label || sourceLabel} -> ${nextProtocol?.label || nextProtocolId} · ${result.label}`,
        'experiment'
      );
      setStatus(`已载入下一协议模板：${nextProtocol?.label || nextProtocolId} / ${result.label}。`, 'ok');
      return result;
    }

    function focusExperimentLibrary(experimentId) {
      previewRoadmapExperiment(experimentId);
    }

    function renderQuickDemoCards() {
      const mainlineStages = showcaseRouteStages();
      const branchStages = showcaseBranchStages();
      const renderDemoCard = ({demoId, config, experimentId = '', status, assetSummary, primaryActionLabel, primaryActionCode, routeLabel = '', routeKind = 'mainline'}) => {
        const scenarioPath = resolveQuickDemoScenario(config);
        const planPath = resolveQuickDemoPlan(config);
        const plan = quickDemoPlanRecord(config);
        const dashboard = plan ? matchingDashboardForPlan(plan) : null;
        const available = Boolean(scenarioPath);
        const effectiveStatus = status || (dashboard
          ? '标准示例结果已就绪'
          : plan
            ? '标准示例计划已固化'
            : available
              ? '可从模板即时生成'
              : '缺少基础场景');
        const actionLabel = primaryActionLabel || (dashboard ? '打开标准结果' : plan ? '打开标准计划' : '一键运行示例');
        const actionCode = primaryActionCode || (dashboard ? `showDashboard('${esc(dashboard.path)}')` : plan ? `showExperiment('${esc(plan.path)}')` : available ? `runQuickExample('${demoId}')` : 'void(0)');
        return `
          <div class="history-detail" style="margin-bottom:0">
            <strong>${esc(config.label)}</strong>
            <p>${esc(config.description)}</p>
            <div class="chips" style="margin-top:8px">
              <span class="chip">${esc(routeKind === 'branch' ? '关键支线' : '固定主线')}</span>
              <span class="chip">${esc(effectiveStatus)}</span>
              ${routeLabel ? `<span class="chip">${esc(routeLabel)}</span>` : ''}
              <span class="chip">场景 ${esc(scenarioPath || '未找到')}</span>
              <span class="chip">计划 ${esc(planPath || config.planName || '未固化')}</span>
              <span class="chip">${esc(config.focus)}</span>
            </div>
            <p style="margin-top:10px"><strong style="display:inline">它回答的问题：</strong> ${esc(config.question || '用最少步骤跑出一组可讲述的实验结果。')}</p>
            <p class="subtle" style="margin-top:8px">${esc(assetSummary || '当前还没有稳定代表资产。')}</p>
            <div class="toolbar" style="margin-top:10px">
              <button type="button" onclick="${actionCode}" ${(dashboard || plan || available || primaryActionCode) ? '' : 'disabled'}>${esc(actionLabel)}</button>
              <button class="secondary" type="button" onclick="${available ? `runQuickExample('${demoId}')` : 'void(0)'}" ${available ? '' : 'disabled'}>运行一个副本</button>
              <button class="secondary" type="button" onclick="${available ? `prepareQuickExample('${demoId}')` : experimentId ? `previewRoadmapExperiment('${experimentId}')` : 'void(0)'}" ${(available || experimentId) ? '' : 'disabled'}>${esc(available ? '载入到创建器' : '定位实验')}</button>
            </div>
          </div>
        `;
      };
      const mainlineCards = mainlineStages.map(stage => renderDemoCard({
        demoId: stage.demoId,
        config: stage.demo,
        experimentId: stage.experimentId,
        status: stage.status,
        assetSummary: `${stage.assetSummary} · ${stage.objective}`,
        primaryActionLabel: stage.primaryActionLabel,
        primaryActionCode: stage.primaryActionCode,
        routeLabel: `${stage.step} ${stage.label}`,
        routeKind: 'mainline',
      })).join('');
      const branchCards = branchStages.map(branch => renderDemoCard({
        demoId: branch.demoId,
        config: branch.demo,
        experimentId: branch.experimentId,
        status: branch.status,
        assetSummary: `${branch.assetSummary} · ${branch.next}`,
        primaryActionLabel: branch.primaryActionLabel,
        primaryActionCode: branch.primaryActionCode,
        routeLabel: `锚点 ${branch.anchorProtocol?.label || branch.routeMeta.routeLabel}`,
        routeKind: 'branch',
      })).join('');
      quickDemoGrid.innerHTML = `
        <div class="history-timeline">
          <h3>固定主线示例</h3>
          <div class="history-detail" style="margin-bottom:10px">
            <strong>先按主线演示</strong>
            <p>第一次演示平台时，先按这 5 组固定主线 demo 走，先把“场景 -> 实验 -> 结果 -> 回放”的主线讲清楚，再进入支线深化。</p>
          </div>
          <div class="intro-grid">${mainlineCards}</div>
          <div class="history-detail" style="margin:10px 0">
            <strong>关键支线示例</strong>
            <p>主线讲清楚之后，再用支线 demo 深入感知链、扰动解释和对地任务，不把支线混成新的主线阶段。</p>
          </div>
          <div class="intro-grid">${branchCards}</div>
        </div>
      `;
    }

    function renderExperimentLibraryCards() {
      const experimentIds = experimentLibraryIds();
      const categories = ['all', 'tuning', 'benchmark', 'robustness', 'environment', 'sensing', 'mission', 'actuator', 'acceptance'];
      const category = categories.includes(state.libraryCategory) ? state.libraryCategory : 'all';
      const filteredIds = experimentIds.filter(id => {
        if (category === 'all') return true;
        return experimentLibraryCategoryId(curatedExperimentConfig(id)) === category;
      });
      const activeId = filteredIds.includes(state.libraryExperimentId)
        ? state.libraryExperimentId
        : filteredIds[0] || experimentIds[0] || '';
      state.libraryCategory = category;
      state.libraryExperimentId = activeId;
      const activeConfig = curatedExperimentConfig(activeId);
      const activeScenarioPath = resolveCuratedExperimentScenario(activeConfig);
      const activePlanPath = resolveCuratedExperimentPlan(activeConfig);
      const activeHasPlan = Boolean(activePlanPath);
      const activeHasScenario = Boolean(activeScenarioPath);
      const categoryMeta = experimentLibraryCategoryMeta(category);
      const detailMeta = experimentLibraryDetailMeta(activeId, activeConfig);
      const academicMeta = experimentAcademicMeta(activeId, activeConfig);
      const representativeDashboard = representativeDashboardForExperiment(activeId);
      const representativeAsset = representativeAssetRecord({experimentId: activeId});
      const activeProtocolId = protocolIdForExperiment(activeId);
      const activeProtocol = experimentProtocolConfig(activeProtocolId);
      const activeProtocolBranches = supportingBranchRouteRecords(activeProtocolId);
      const primaryProtocolBranch = activeProtocolBranches[0] || null;
      const nextProtocol = experimentProtocolConfig(activeProtocol?.nextProtocolId || '');
      const nextProtocolExperimentId = protocolPrimaryExperimentId(activeProtocol?.nextProtocolId || '', 'all');
      const protocolButtons = experimentProtocolIds(category).map(id => {
        const protocol = experimentProtocolConfig(id);
        const count = protocolExperimentIds(id, category).length;
        return `
          <button class="library-category-button ${id === activeProtocolId ? 'active' : ''}" type="button" onclick="selectLibraryProtocol('${id}')">
            <strong>${esc(protocol?.label || id)}</strong>
            <span>${esc(protocol?.summary || '当前协议会把成熟实验串成一条标准研究流程。')} 当前 ${count} 个实验。</span>
          </button>
        `;
      }).join('');
      const categoryButtons = categories.map(id => {
        const meta = experimentLibraryCategoryMeta(id);
        const count = experimentIds.filter(expId => id === 'all' || experimentLibraryCategoryId(curatedExperimentConfig(expId)) === id).length;
        return `
          <button class="library-category-button ${id === category ? 'active' : ''}" type="button" onclick="setLibraryCategory('${id}')">
            <strong>${esc(meta.label)}</strong>
            <span>${esc(meta.description)} 当前 ${count} 个实验。</span>
          </button>
        `;
      }).join('');
      const experimentButtons = filteredIds.map(id => {
        const config = curatedExperimentConfig(id);
        const scenarioPath = resolveCuratedExperimentScenario(config);
        const planPath = resolveCuratedExperimentPlan(config);
        const availableText = planPath ? '已固化计划' : scenarioPath ? '可由场景生成' : '工作区缺少场景';
        return `
          <button class="library-experiment-button ${id === activeId ? 'active' : ''}" type="button" onclick="selectLibraryExperiment('${id}')">
            <strong>${esc(config.label)}</strong>
            <span>${esc(config.theme || '实验模板')} · ${esc(config.variable)} · ${esc(availableText)}</span>
          </button>
        `;
      }).join('');
      const experimentPreviewCards = filteredIds.slice(0, 8).map(id => {
        const config = curatedExperimentConfig(id);
        const scenarioPath = resolveCuratedExperimentScenario(config);
        const representative = representativeAssetRecord({experimentId: id});
        const resultText = representative.dashboard
          ? `代表结果已就绪 · ${representativeDashboardSummary(representative.dashboard)}`
          : representative.plan
            ? `已有代表计划 ${representative.plan.name}，结果待补`
            : (scenarioPath ? '可从当前场景直接生成第一版计划' : '当前工作区缺少对应场景');
        return `
          <button class="history-card ${id === activeId ? 'active' : ''}" type="button" onclick="selectLibraryExperiment('${id}')">
            <span>${esc(id === activeId ? '当前实验' : (config.theme || '实验模板'))}</span>
            <strong>${esc(config.label)}</strong>
            <p>${esc(config.question || config.description || '当前实验用于回答某一类姿态控制问题。')}<br>${esc(resultText)}</p>
          </button>
        `;
      }).join('');
      const matureReference = category === 'environment'
        ? '成熟范式映射：对应 Tudat 的 environment setup / output 分层。'
        : category === 'mission'
          ? '成熟范式映射：对应 GMAT mission sequence 与 Basilisk runtime 的展示入口。'
        : category === 'actuator'
          ? '成熟范式映射：对应 Basilisk 风格执行机构与状态效应器边界。'
        : category === 'acceptance'
          ? '成熟范式映射：对应实验平台中的评估口径、验收门限和结果可复查标准。'
          : '成熟范式映射：先把实验模板、变量和结果讲清楚，再继续扩高保真与产品化。';
      const protocolReference = activeProtocol?.matureReference || matureReference;
      const representativeProtocolExperiments = (activeProtocol?.representativeExperimentIds || [])
        .map(id => curatedExperimentConfig(id)?.label || id)
        .join(' / ');
      const standardMeta = experimentStandardCardMeta(activeId, activeConfig || {planName: activeId, theme: ''}, academicMeta);
      const assetBinding = standardAssetBindingRecord({experimentId: activeId});
      const briefMeta = experimentBriefMeta({
        experimentId: activeId,
        config: activeConfig,
        detailMeta,
        academicMeta,
        standardMeta,
        scenarioPath: activeScenarioPath,
        templateConfig: activeConfig?.templateId ? experimentTemplateConfig(activeConfig.templateId) : null,
        sweepPath: activeConfig?.templateId
          ? sweepPresetConfig(experimentTemplateConfig(activeConfig.templateId)?.sweepPreset || 'custom').path
          : '',
        variableLabel: activeConfig?.variable || '',
      });
      const readingTask = experimentReadingTaskMeta({
        experimentId: activeId,
        config: activeConfig,
        detailMeta,
        academicMeta,
        standardMeta,
        deliverables: activeProtocol?.outputs || activeConfig?.outputs || standardMeta.figurePackage,
        nextAction: nextProtocol ? `${nextProtocol.label}：${nextProtocol.summary}` : detailMeta.next,
      });
      const detailView = ['protocol', 'assets', 'academic'].includes(state.libraryDetailView) ? state.libraryDetailView : 'protocol';
      state.libraryDetailView = detailView;
      const detailWorkbenchNodes = libraryDetailWorkbenchNodes({
        protocolLabel: activeProtocol?.label || '当前协议',
        protocolObjective: activeProtocol?.objective || activeConfig?.question || '当前协议负责把实验放回更长期的平台研究链路中理解。',
        representativePlan: representativeAsset.plan,
        representativeDashboard,
        academicTakeaway: academicMeta.takeaway || academicMeta.hypothesis,
      });
      const detailViewMarkup = detailView === 'assets'
        ? `
          <div class="history-detail" style="margin-top:12px; margin-bottom:0">
            <strong>代表资产视图</strong>
            <p>这里专门回答“当前有没有稳定代表计划、有没有代表结果、下一步该继续跑还是继续读图”，不再把协议和学术说明混在一起。</p>
            <div class="detail-grid" style="margin-top:10px">
              <div class="detail-box">
                <strong>推荐验收</strong>
                <div>${esc(standardMeta.acceptance)}</div>
              </div>
              <div class="detail-box">
                <strong>推荐结果图包</strong>
                <div>${esc(standardMeta.figurePackage)}</div>
              </div>
              <div class="detail-box">
                <strong>实验基线</strong>
                <div>${esc(detailMeta.baseline)}</div>
              </div>
              <div class="detail-box">
                <strong>推荐下一步</strong>
                <div>${esc(detailMeta.next)}</div>
              </div>
              <div class="detail-box">
                <strong>平台价值</strong>
                <div>${esc(detailMeta.platform)}</div>
              </div>
              <div class="detail-box">
                <strong>资产定位</strong>
                <div>${esc(standardMeta.assetRole)}</div>
              </div>
              <div class="detail-box">
                <strong>代表计划</strong>
                <div>${representativeAsset.plan
                  ? `${esc(representativeAsset.plan.name)} · ${esc(experimentScaleText(representativeAsset.plan))}`
                  : (activeHasPlan ? `${esc(activeConfig?.planName || activeId)} · 已固化但尚未在工作区识别` : '当前还没有对应代表计划。')}</div>
              </div>
              <div class="detail-box">
                <strong>代表结果入口</strong>
                <div>${representativeAsset.dashboard
                  ? `${esc(representativeAsset.dashboard.name || activeConfig?.label || activeId)} · ${esc(representativeDashboardSummary(representativeAsset.dashboard))}`
                  : '当前还没有匹配到代表结果。建议先运行该实验，后续就可以从实验库直接跳到样例结果。'}</div>
              </div>
              <div class="detail-box">
                <strong>当前阶段</strong>
                <div>${esc(representativeAsset.status)} · ${esc(representativeAsset.standardMeta.assetRole)}</div>
              </div>
              <div class="detail-box">
                <strong>推荐动作</strong>
                <div>${esc(representativeAsset.next)}</div>
              </div>
              <div class="detail-box">
                <strong>派生命名建议</strong>
                <div>${esc(standardMeta.derivedPlan)}</div>
              </div>
              <div class="detail-box">
                <strong>实验成熟度</strong>
                <div>${esc(standardMeta.maturity)}</div>
              </div>
            </div>
            <div class="toolbar" style="margin-top:10px">
              <button class="secondary" type="button" onclick="${representativeAsset.plan ? `showExperiment('${esc(representativeAsset.plan.path)}')` : 'void(0)'}" ${representativeAsset.plan ? '' : 'disabled'}>打开代表计划</button>
              <button class="secondary" type="button" onclick="${representativeAsset.plan ? `runPlan('${esc(representativeAsset.plan.path)}')` : 'void(0)'}" ${representativeAsset.plan ? '' : 'disabled'}>运行代表计划</button>
              <button class="secondary" type="button" onclick="${representativeAsset.dashboard ? `showDashboard('${esc(representativeAsset.dashboard.path)}')` : 'void(0)'}" ${representativeAsset.dashboard ? '' : 'disabled'}>查看代表结果</button>
            </div>
            ${standardAssetBindingHtml(assetBinding, {title: '当前实验的标准资产绑定'})}
          </div>
          `
        : detailView === 'academic'
          ? `
            ${experimentAcademicShowcaseHtml(activeConfig, academicMeta, activeProtocol)}
            <div class="history-detail" style="margin-top:12px; margin-bottom:0">
              <strong>学术说明</strong>
              <p>这里专门收口实验假设、适用前提、建议图表和成熟平台映射，更适合按论文实验或技术报告的节奏阅读。</p>
              <div class="detail-grid" style="margin-top:10px">
                <div class="detail-box">
                  <strong>实验假设</strong>
                  <div>${esc(academicMeta.hypothesis)}</div>
                </div>
                <div class="detail-box">
                  <strong>适用前提</strong>
                  <div>${esc(academicMeta.prerequisites)}</div>
                </div>
                <div class="detail-box">
                  <strong>建议图表</strong>
                  <div>${esc(academicMeta.plots)}</div>
                </div>
                <div class="detail-box">
                  <strong>典型结论线索</strong>
                  <div>${esc(academicMeta.takeaway)}</div>
                </div>
                <div class="detail-box">
                  <strong>成熟平台对应</strong>
                  <div>${esc(protocolReference)}</div>
                  <div class="subtle" style="margin-top:8px">${esc(categoryMeta.reference)}</div>
                </div>
                <div class="detail-box">
                  <strong>适用时机</strong>
                  <div>${esc(activeConfig?.whenToUse || '适合把场景、变量和验收组织成稳定实验模板。')}</div>
                </div>
                <div class="detail-box">
                  <strong>协议判定</strong>
                  <div>${esc(activeProtocol?.decision || '当前实验应能给出下一阶段是否继续推进的判断。')}</div>
                </div>
                <div class="detail-box">
                  <strong>协议代表实验</strong>
                  <div>${esc(representativeProtocolExperiments || activeConfig?.label || '当前实验')}</div>
                </div>
              </div>
            </div>
          `
          : `
            <div class="history-detail" style="margin-top:12px; margin-bottom:0">
              <strong>协议概览</strong>
              <p>这里先固定“研究问题 -> 推荐步骤 -> 交付物 -> 下一协议”的协议语义，再决定是否继续看资产或学术细节。</p>
              <div class="detail-grid" style="margin-top:10px">
                <div class="detail-box">
                  <strong>协议目标</strong>
                  <div>${esc(activeProtocol?.objective || '当前协议负责把实验放回更长期的平台研究链路中理解。')}</div>
                </div>
                <div class="detail-box">
                  <strong>进入条件</strong>
                  <div>${esc(activeProtocol?.prerequisites || '需要一个清楚的基线场景、变量和结果口径。')}</div>
                </div>
                <div class="detail-box">
                  <strong>协议推荐步骤</strong>
                  <div>${esc(protocolStepsText(activeProtocol))}</div>
                </div>
                <div class="detail-box">
                  <strong>协议交付物</strong>
                  <div>${esc(activeProtocol?.outputs || 'README / index / summary_metrics / dashboard')}</div>
                </div>
                <div class="detail-box">
                  <strong>下一条协议</strong>
                  <div>${esc(nextProtocol
                    ? `${nextProtocol.label}：${nextProtocol.summary}`
                    : '当前协议已经收束，适合回到结果页整理摘要，或把当前实验沉淀成代表性结果。')}</div>
                </div>
                <div class="detail-box">
                  <strong>关键支线</strong>
                  <div>${esc(activeProtocolBranches.length ? activeProtocolBranches.map(branch => `${branch.label}：${branch.summary}`).join('；') : '当前协议没有单独抽出的关键支线。')}</div>
                </div>
                <div class="detail-box">
                  <strong>协议代表实验</strong>
                  <div>${esc(representativeProtocolExperiments || activeConfig?.label || '当前实验')}</div>
                </div>
              </div>
            </div>
          `;
      experimentLibraryGrid.innerHTML = `
        <div class="history-timeline">
          <h3>推荐实验库</h3>
          <div class="library-shell">
            <div class="library-panel">
              <h3>实验协议主线</h3>
              <p class="library-caption">实验库首屏先按协议主线组织，而不是先让人掉进一长串模板列表。先锁定主线，再决定看代表资产、现有计划还是细节实验。</p>
              <div class="library-category-list">${protocolButtons || '<div class="empty">当前分组暂时没有可展示的实验协议。</div>'}</div>
              <div class="history-detail" style="margin-top:12px; margin-bottom:0">
                <strong>当前协议实验速览</strong>
                <p>不再把候选实验只藏在折叠区里。先从这里切一个焦点实验，再去右侧读协议、资产和学术说明。</p>
                <div class="history-grid" style="margin-top:10px">${experimentPreviewCards || '<div class="detail-box"><strong>当前分组</strong><div>当前分组暂时没有可展示实验。</div></div>'}</div>
              </div>
              <details class="fold-panel" style="margin-top:12px">
                <summary>实验分组<span>按研究问题筛选协议与实验类型</span></summary>
                <div class="fold-panel-body">
                  <p class="library-caption" style="margin-top:10px">先按研究问题选择实验类型，再在下方挑一个具体实验。这样平台会更像实验目录，而不是散落的 JSON 文件列表。</p>
                  <div class="library-category-list">${categoryButtons}</div>
                </div>
              </details>
              <details class="fold-panel" style="margin-top:12px" open>
                <summary>当前分组实验<span>${esc(categoryMeta.label)} · ${esc(categoryMeta.description)}</span></summary>
                <div class="fold-panel-body">
                  <div class="library-experiment-list" style="margin-top:10px">${experimentButtons || '<div class="empty">当前分组暂时没有实验。</div>'}</div>
                </div>
              </details>
            </div>
            <div class="library-panel">
              <h3>${esc(activeConfig?.label || '实验详情')}</h3>
              <p class="library-detail-lead">${esc(activeConfig?.description || '选择一个实验后，这里会显示它的研究问题、变量、结果产物和推荐下一步。')}</p>
              <div class="chips" style="margin-top:10px">
                <span class="chip">主题 ${esc(activeConfig?.theme || '实验模板')}</span>
                <span class="chip">场景 ${esc(activeScenarioPath || '未找到')}</span>
                <span class="chip">变量 ${esc(activeConfig?.variable || '—')}</span>
                <span class="chip">计划 ${esc(activePlanPath || '待生成')}</span>
              </div>
              <div class="callout info" style="margin-top:12px; margin-bottom:0">
                <strong>实验协议卡</strong>
                <p>${esc(activeProtocol?.summary || '当前实验已被放回一条标准研究协议中，方便继续沿同一主线扩展。')}</p>
                <div class="chips" style="margin-top:8px">
                  <span class="chip">协议 ${esc(activeProtocol?.label || '未指定')}</span>
                  <span class="chip">入口 ${esc(protocolPrimaryExperimentLabel(activeProtocolId))}</span>
                  <span class="chip">下一协议 ${esc(nextProtocol?.label || '当前主线收束')}</span>
                  <span class="chip">支线 ${esc(activeProtocolBranches.length ? activeProtocolBranches.map(branch => branch.label).join(' / ') : '无')}</span>
                </div>
              </div>
              <div class="summary-grid" style="margin-top:12px">
                <div class="summary-card"><span>当前实验</span><strong>${esc(activeConfig?.label || activeId)}</strong></div>
                <div class="summary-card"><span>协议阶段</span><strong>${esc(standardMeta.maturity)}</strong></div>
                <div class="summary-card"><span>代表资产</span><strong>${esc(representativeAsset.dashboard ? '结果已就绪' : representativeAsset.plan ? '计划已就绪' : '待落地')}</strong></div>
                <div class="summary-card"><span>下一协议</span><strong>${esc(nextProtocol?.label || '当前收束')}</strong></div>
                <div class="summary-card"><span>关键支线</span><strong>${esc(activeProtocolBranches.length ? activeProtocolBranches.map(branch => branch.label).join(' / ') : '无')}</strong></div>
              </div>
              <div class="history-detail" style="margin:12px 0 0">
                <strong>实验库阅读地图</strong>
                <p>右侧详情先固定阅读顺序：先看协议，再看资产，最后核学术口径。这样模板就不会重新堆成一页散点信息。</p>
                <div class="flow-sequence" style="margin-top:10px">
                  ${detailWorkbenchNodes.map(node => `
                    <div class="flow-node ${esc(node.tone)}">
                      <small>${esc(node.step)}</small>
                      <strong>${esc(node.title)}</strong>
                      <p>${esc(node.body)}</p>
                      <div class="toolbar" style="margin-top:10px">
                        <button type="button" onclick="setLibraryDetailView('${esc(node.action)}')">${esc(node.label)}</button>
                      </div>
                    </div>
                  `).join('')}
                </div>
              </div>
               ${experimentBriefHtml(briefMeta, {
                 title: '实验任务说明卡',
                 lead: '实验库不再只列模板名，而是固定回答 docs 里要求的 7 个说明项。创建器和结果页会沿用同一套口径。',
                 compact: true,
                })}
               ${experimentReadingTaskHtml(readingTask, {
                 title: '结果阅读任务书',
                 lead: '实验库直接给出跑完后推荐的读图顺序、结论焦点和下一步实验，不让用户自己摸索阅读路线。',
                 compact: true,
               })}
                ${standardAssetBindingHtml(assetBinding, {title: '主线代表资产', showToolbar: true})}
              <div class="editor-view-shell" style="margin-top:12px">
                <div class="segment-control">
                  <button class="${detailView === 'protocol' ? 'active' : ''}" type="button" onclick="setLibraryDetailView('protocol')">协议概览</button>
                  <button class="${detailView === 'assets' ? 'active' : ''}" type="button" onclick="setLibraryDetailView('assets')">代表资产</button>
                  <button class="${detailView === 'academic' ? 'active' : ''}" type="button" onclick="setLibraryDetailView('academic')">学术说明</button>
                </div>
                <div class="editor-view-note">${esc(libraryDetailViewNote(detailView))}</div>
              </div>
              ${detailViewMarkup}
               <div class="toolbar library-detail-actions">
                 <button type="button" onclick="${activeHasPlan ? `showExperiment('${activePlanPath}')` : 'void(0)'}" ${activeHasPlan ? '' : 'disabled'}>打开现有计划</button>
                 <button class="secondary" type="button" onclick="${activeHasScenario ? `prepareCuratedExperiment('${activeId}')` : 'void(0)'}" ${activeHasScenario ? '' : 'disabled'}>载入同类模板</button>
                 <button class="secondary" type="button" onclick="${activeProtocolId ? `prepareNextProtocolExperiment('${esc(activeProtocolId)}', '实验库协议卡：')` : 'void(0)'}" ${(activeProtocolId && nextProtocolExperimentId) ? '' : 'disabled'}>载入下一协议模板</button>
                 <button class="secondary" type="button" onclick="${primaryProtocolBranch ? `previewRoadmapExperiment('${esc(primaryProtocolBranch.experimentId)}')` : 'void(0)'}" ${primaryProtocolBranch ? '' : 'disabled'}>定位关键支线</button>
                 <button class="secondary" type="button" onclick="${activeHasPlan ? `runPlan('${activePlanPath}')` : 'void(0)'}" ${activeHasPlan ? '' : 'disabled'}>运行当前实验</button>
                 <button class="secondary" type="button" onclick="${representativeDashboard ? `showDashboard('${esc(representativeDashboard.path)}')` : 'void(0)'}" ${representativeDashboard ? '' : 'disabled'}>查看代表结果</button>
                 <button class="secondary" type="button" onclick="${nextProtocolExperimentId ? `previewRoadmapExperiment('${nextProtocolExperimentId}')` : 'void(0)'}" ${nextProtocolExperimentId ? '' : 'disabled'}>定位下一协议实验</button>
               </div>
            </div>
          </div>
        </div>
      `;
    }

    function setLibraryCategory(categoryId) {
      state.libraryCategory = categoryId || 'all';
      const filteredIds = experimentLibraryIds().filter(id => state.libraryCategory === 'all' || experimentLibraryCategoryId(curatedExperimentConfig(id)) === state.libraryCategory);
      state.libraryExperimentId = filteredIds[0] || '';
      state.libraryView = 'template';
      renderExperimentLibraryCards();
      renderLibraryView();
    }

    function selectLibraryProtocol(protocolId) {
      const firstId = protocolPrimaryExperimentId(protocolId, state.libraryCategory || 'all');
      if (!firstId) return;
      const config = curatedExperimentConfig(firstId);
      state.libraryCategory = experimentLibraryCategoryId(config);
      state.libraryExperimentId = firstId;
      state.libraryView = 'template';
      renderExperimentLibraryCards();
      renderLibraryView();
    }

    function selectLibraryExperiment(experimentId) {
      state.libraryExperimentId = experimentId || '';
      state.libraryView = 'template';
      renderExperimentLibraryCards();
      renderLibraryView();
    }

    function renderQuickDemoStatus() {
      const session = state.quickDemoSession;
      if (!session) {
        quickDemoStatus.innerHTML = `
          <div class="callout" style="margin-bottom:0">
            <strong>示例运行助手</strong>
            <p>从上面的推荐示例里选择一个，就可以自动载入场景、模板并直接跑出可展示的结果。</p>
          </div>
        `;
        return;
      }
      const statusText = {
        prepared: '已载入到创建器',
        creating: '正在创建示例计划',
        running: '正在运行示例',
        completed: '示例运行完成',
        error: '示例运行遇到问题',
      }[session.status] || '示例处理中';
      const toneClass = session.status === 'completed' ? 'callout success'
        : session.status === 'error' ? 'callout danger'
        : 'callout info';
      const actions = [];
      if (session.planPath) {
        actions.push(`<button type="button" onclick="showExperiment('${esc(session.planPath)}')">查看计划</button>`);
      }
      if (session.dashboardPath) {
        actions.push(`<button class="secondary" type="button" onclick="showDashboard('${esc(session.dashboardPath)}')">查看结果</button>`);
      }
      if (session.demoId) {
        actions.push(`<button class="secondary" type="button" onclick="prepareQuickExample('${esc(session.demoId)}')">重新载入示例</button>`);
      }
      const metricLine = session.runCount
        ? `本次共生成 ${session.runCount} 个 run${session.bestFinalErrorDeg !== undefined && session.bestFinalErrorDeg !== null ? `，最佳末端误差 ${fmt(session.bestFinalErrorDeg)} deg` : ''}。`
        : '当前正在准备或运行示例。';
      quickDemoStatus.innerHTML = `
        <div class="${toneClass}" style="margin-bottom:0">
          <strong>示例运行助手 · ${esc(session.label || '当前示例')}</strong>
          <p>${esc(statusText)}。${esc(session.description || '')}</p>
          <p style="margin-top:8px">${esc(metricLine)}${session.outputRoot ? ` 输出目录 ${esc(session.outputRoot)}。` : ''}</p>
          <div class="chips" style="margin-top:8px">
            <span class="chip">状态 ${esc(statusText)}</span>
            ${session.planPath ? `<span class="chip">计划 ${esc(session.planPath)}</span>` : ''}
            ${session.dashboardPath ? `<span class="chip">结果 ${esc(session.dashboardPath)}</span>` : ''}
          </div>
          <div class="toolbar" style="margin-top:10px">
            ${actions.join('') || '<button type="button" disabled>等待中</button>'}
          </div>
        </div>
      `;
    }

    function modeExplanation(mode) {
      const mapping = {
        inertial_hold: '惯性保持：目标是在惯性空间稳定维持姿态，适合做基线稳定控制验证。',
        sun_pointing: '太阳指向：目标是把机体参考轴对准太阳方向，适合做姿态跟踪与模式切换展示。',
        earth_pointing: '对地指向：目标是把机体参考轴对准地球方向，适合任务载荷或成像场景演示。',
        safe: '安全模式：目标是进入保守姿态和安全控制状态，适合展示应急或低功耗模式。',
      };
      return mapping[mode] || '当前模式用于定义控制器整个实验期间或保持阶段的参考任务姿态。';
    }

    function inferSweepMeaning(path) {
      const text = String(path || '').trim();
      if (!text) return '当前未启用参数扫描，只会运行单一配置。';
      if (text.startsWith('controller.')) return '当前扫描的是控制器参数 `' + text + '`，适合比较控制律整定效果。';
      if (text === 'system.controller') return '当前扫描的是控制器类型，适合在统一场景和任务下做 PD、LADRC 等控制律 benchmark 对比。';
      if (text === 'system.environment') return '当前扫描的是环境配置，适合比较理想零扰动和轨道环境扰动对闭环误差、力矩和扰动预算的影响。';
      if (text === 'system.disturbance_profile') return '当前扫描的是扰动配置模板，适合把重力梯度、残余磁矩、气动和太阳压逐项拆开比较。';
      if (text === 'sensors.gyro.noise_std_rad_s') return '当前扫描的是陀螺测量噪声，适合比较测量质量退化后误差和通过率的变化。';
      if (text.startsWith('time.')) return '当前扫描的是时间或随机性参数 `' + text + '`，适合做可重复性和种子敏感性分析。';
      if (text === 'actuators.reaction_wheels.momentum_gain') return '当前扫描的是轮组动量管理增益，适合比较轮速回拉策略对姿态保持和执行器余量的影响。';
      if (text.includes('reaction_wheels')) return '当前扫描的是执行机构参数 `' + text + '`，适合比较力矩能力或轮组配置变化。';
      return '当前扫描的是配置路径 `' + text + '`，平台会在每个候选值上生成一组独立 run。';
    }

    function renderBuilderTemplateBrowser() {
      if (!builderTemplateGrid || !builderCategoryNav || !builderCategoryCopy) return;
      const category = state.builderCategory || 'all';
      const meta = builderCategoryMeta(category);
      builderCategoryCopy.textContent = `当前主线：${meta.label}。${meta.description}`;
      builderCategoryNav.querySelectorAll('[data-builder-category]').forEach(button => {
        button.classList.toggle('active', (button.dataset.builderCategory || 'all') === category);
      });
      builderTemplateGrid.querySelectorAll('.template-card').forEach(card => {
        const cardCategory = card.dataset.builderCategory || 'all';
        card.hidden = !(category === 'all' || cardCategory === category);
      });
    }

    function setBuilderCategory(categoryId) {
      state.builderCategory = categoryId || 'all';
      renderBuilderTemplateBrowser();
    }

    function currentBuilderTemplateId() {
      if (builderMission.value === 'detumble_then_hold' && builderMode.value === 'sun_pointing' && builderReference.value === 'sun') {
        return 'sun_transition';
      }
      if (builderMission.value === 'detumble_then_hold' && builderMode.value === 'earth_pointing' && builderReference.value === 'nadir') {
        return 'earth_transition';
      }
      if (builderSweepPreset.value === 'system.controller') {
        return 'controller_benchmark';
      }
      if (builderSweepPreset.value === 'system.environment') {
        return 'environment_sensitivity';
      }
      if (builderSweepPreset.value === 'system.disturbance_profile') {
        return builderSecondSweepPreset.value === 'actuators.reaction_wheels.max_torque_nm' && parseBuilderValues(builderSecondSweepValues.value).length
          ? 'disturbance_capability_tradeoff'
          : 'disturbance_breakdown';
      }
      if (builderSweepPreset.value === 'sensors.gyro.noise_std_rad_s') {
        return 'sensor_sensitivity';
      }
      if (builderSweepPreset.value === 'actuators.reaction_wheels.momentum_gain') {
        return 'momentum_management';
      }
      if (builderSweepPreset.value === 'time.seed' && Number(builderMcSamples.value || 0) > 0) {
        return 'mc_robustness';
      }
      if (builderSweepPreset.value === 'actuators.reaction_wheels.max_torque_nm') {
        return 'wheel_capability';
      }
      if (builderSweepPreset.value === 'controller.pd_kp') {
        return (builderAcceptancePreset.value || 'standard_hold') === 'strict_hold'
          ? 'acceptance_gate'
          : 'pd_tuning';
      }
      if (builderSweepPreset.value === 'controller.pd_kd') {
        return builderMission.value === 'detumble_then_hold' && builderMode.value === 'earth_pointing'
          ? 'earth_transition'
          : builderMission.value === 'detumble_then_hold'
            ? 'sun_transition'
            : 'pd_tuning';
      }
      return '';
    }

    function builderSnapshot() {
      const mission = builderMission.value;
      const preset = builderSweepPreset.value || 'custom';
      const presetConfig = sweepPresetConfig(preset);
      const secondPreset = builderSecondSweepPreset.value || 'custom';
      const secondPresetConfig = sweepPresetConfig(secondPreset);
      const acceptancePreset = builderAcceptancePreset.value || 'standard_hold';
      const acceptanceConfig = acceptancePresetConfig(acceptancePreset);
      const sweepValues = parseBuilderValues(builderSweepValues.value);
      const secondSweepValues = parseBuilderValues(builderSecondSweepValues.value);
      const sweepCount = sweepValues.length || 1;
      const secondSweepEnabled = Boolean(String(builderSecondSweepPath.value || '').trim() && secondSweepValues.length);
      const secondSweepCount = secondSweepEnabled ? secondSweepValues.length : 1;
      const mcSamples = Math.max(Number(builderMcSamples.value || 0), 0);
      const mcCount = mcSamples > 0 ? mcSamples : 1;
      const runCount = sweepCount * secondSweepCount * mcCount;
      const scenario = state.workspace?.scenarios?.find(item => item.path === builderScenario.value);
      const scenarioName = scenario?.name || '当前场景';
      const description = String(builderDescription.value || '').trim();
      const acceptFinal = Number(builderAcceptFinal.value || 0);
      const acceptRms = Number(builderAcceptRms.value || 0);
      const acceptTorque = Number(builderAcceptTorque.value || 0);
      const templateId = currentBuilderTemplateId();
      const templateConfig = experimentTemplateConfig(templateId);
      const protocol = experimentProtocolConfig(protocolIdForTemplate(templateId));
      const nextProtocol = experimentProtocolConfig(protocol?.nextProtocolId || '');
      const stage = state.builderStage || 'question';
      const stageLabel = {
        question: '研究问题',
        variables: '场景与变量',
        runtime: '任务与验收',
        review: '预览与生成',
      }[stage] || '研究问题';
      const suggestedOutput = builderOutput.value.trim() || `results/platform_ui/${(builderName.value || scenarioName || 'experiment').trim().replace(/\\s+/g, '_')}`;
      return {
        mission,
        preset,
        presetConfig,
        secondPreset,
        secondPresetConfig,
        acceptancePreset,
        acceptanceConfig,
        sweepValues,
        secondSweepValues,
        sweepCount,
        secondSweepEnabled,
        secondSweepCount,
        mcSamples,
        mcCount,
        runCount,
        scenario,
        scenarioName,
        description,
        acceptFinal,
        acceptRms,
        acceptTorque,
        templateId,
        templateConfig,
        protocol,
        nextProtocol,
        stage,
        stageLabel,
        suggestedOutput,
      };
    }

    function builderProtocolBundle(snapshot) {
      const protocolId = protocolIdForTemplate(snapshot.templateId);
      const representativeId = protocolPrimaryExperimentId(protocolId, 'all');
      const representativeConfig = curatedExperimentConfig(representativeId);
      const representativePlanPath = representativeConfig ? resolveCuratedExperimentPlan(representativeConfig) : '';
      const representativePlan = representativePlanPath
        ? [...(state.workspace?.experiments || [])].find(plan => plan.path === representativePlanPath) || null
        : null;
      const representativeDashboard = representativePlan ? matchingDashboardForPlan(representativePlan) : null;
      const representativeAsset = representativeId ? representativeAssetRecord({experimentId: representativeId}) : representativeAssetRecord({});
      const academicMeta = experimentAcademicMeta(
        representativeId,
        representativeConfig || {theme: snapshot.templateConfig?.label || snapshot.protocol?.label || ''},
      );
      const standardMeta = experimentStandardCardMeta(
        representativeId,
        representativeConfig || snapshot.templateConfig || {planName: snapshot.templateId, theme: ''},
        academicMeta,
      );
      const assetBinding = representativeId
        ? standardAssetBindingRecord({experimentId: representativeId})
        : standardAssetBindingRecord({});
      const nextProtocolExperimentId = protocolPrimaryExperimentId(snapshot.protocol?.nextProtocolId || '', 'all');
      const primaryBranch = supportingBranchRouteRecords(protocolId)[0] || null;
      return {
        protocolId,
        representativeId,
        representativeConfig,
        representativePlanPath,
        representativePlan,
        representativeDashboard,
        representativeAsset,
        academicMeta,
        standardMeta,
        assetBinding,
        nextProtocolExperimentId,
        primaryBranch,
      };
    }

    function renderBuilderStageBriefs(snapshot) {
      const {
        mission,
        presetConfig,
        secondPresetConfig,
        acceptanceConfig,
        sweepValues,
        secondSweepValues,
        secondSweepEnabled,
        mcSamples,
        runCount,
        scenarioName,
        description,
        acceptFinal,
        acceptRms,
        acceptTorque,
        templateConfig,
        protocol,
        nextProtocol,
        suggestedOutput,
      } = snapshot;
      const {
        representativeId,
        representativeConfig,
        representativePlanPath,
        representativePlan,
        representativeDashboard,
        representativeAsset,
        academicMeta,
        standardMeta,
        assetBinding,
        nextProtocolExperimentId,
        primaryBranch,
      } = builderProtocolBundle(snapshot);
      const activePrimaryPreset = presetConfig.valuePresets.find(item => item.values === String(builderSweepValues.value || '').trim())
        || presetConfig.valuePresets[0]
        || null;
      const activeSecondaryPreset = secondSweepEnabled
        ? (secondPresetConfig.valuePresets.find(item => item.values === String(builderSecondSweepValues.value || '').trim())
          || secondPresetConfig.valuePresets[0]
          || null)
        : null;
      const primaryValuesText = sweepValues.length
        ? sweepValues.join('、')
        : (activePrimaryPreset?.values || '当前按单一配置运行');
      const secondValuesText = secondSweepEnabled
        ? secondSweepValues.join('、')
        : '未启用第二维';
      const missionFlowLabel = mission === 'detumble_then_hold' ? '消旋后保持' : '单模式保持';
      const protocolDecision = protocol?.decision || academicMeta?.takeaway || '先形成第一版标准结果，再回到主线继续推进。';

      if (builderVariableBrief) {
        builderVariableBrief.innerHTML = `
          <strong>变量设计任务书</strong>
          <p>把主变量、推荐取值、二维扩展和结果图包压成同一张设计卡，先确认实验结构，再继续调字段。</p>
          <div class="summary-grid" style="margin-top:10px">
            <div class="summary-card"><span>主变量</span><strong>${esc(presetConfig.label || '单场景')}</strong></div>
            <div class="summary-card"><span>推荐取值</span><strong>${esc(activePrimaryPreset?.label || (sweepValues.length ? `${sweepValues.length} 个值` : '手动填写'))}</strong></div>
            <div class="summary-card"><span>二维扩展</span><strong>${esc(secondSweepEnabled ? (activeSecondaryPreset?.label || secondPresetConfig.label || '已启用') : '未启用')}</strong></div>
            <div class="summary-card"><span>总 run</span><strong>${esc(runCount)}</strong></div>
          </div>
          <div class="flow-sequence" style="margin-top:12px">
            <div class="flow-node accent">
              <small>1. 主变量</small>
              <strong>${esc(presetConfig.label || '当前变量')}</strong>
              <p>${esc(inferSweepMeaning(builderSweepPath.value).replace('当前', '这里'))}</p>
            </div>
            <div class="flow-node">
              <small>2. 推荐取值</small>
              <strong>${esc(activePrimaryPreset?.label || '当前取值')}</strong>
              <p>${esc(activePrimaryPreset?.help || `当前取值为 ${primaryValuesText}。`)}</p>
            </div>
            <div class="flow-node ${secondSweepEnabled || mcSamples > 0 ? 'warm' : ''}">
              <small>3. 扩展维度</small>
              <strong>${esc(secondSweepEnabled ? (secondPresetConfig.label || '第二变量') : mcSamples > 0 ? 'Monte Carlo' : '当前保持单维')}</strong>
              <p>${esc(secondSweepEnabled
                ? `第二维取值 ${secondValuesText}，会与第一维做组合。`
                : mcSamples > 0
                  ? `当前会按 ${mcSamples} 个随机样本展开。`
                  : '当前先保持一维变量，更适合快速读图和形成首版结论。')}</p>
            </div>
            <div class="flow-node ok">
              <small>4. 结果图包</small>
              <strong>${esc(standardMeta.figurePackage)}</strong>
              <p>${esc(protocolDecision)}</p>
            </div>
          </div>
          <div class="detail-grid" style="margin-top:12px">
            <div class="detail-box"><strong>推荐变量设计</strong><div>${esc(`${presetConfig.label || '当前变量'} · ${primaryValuesText}`)}</div></div>
            <div class="detail-box"><strong>推荐二维扩展</strong><div>${esc(secondSweepEnabled ? `${secondPresetConfig.label || '第二变量'} · ${secondValuesText}` : '当前不必立即启用二维实验，先形成一版一维结论更稳。')}</div></div>
            <div class="detail-box"><strong>研究结论目标</strong><div>${esc(academicMeta.takeaway || '当前实验用于形成一版可复查的变量比较结论。')}</div></div>
            <div class="detail-box"><strong>代表实验参考</strong><div>${esc(representativeConfig?.label || templateConfig?.label || '当前模板')}</div></div>
          </div>
          ${standardAssetBindingHtml(assetBinding, {
            title: '变量阶段主线资产链',
            showToolbar: false,
          })}
        `;
      }

      if (builderRuntimeBrief) {
        builderRuntimeBrief.innerHTML = `
          <strong>任务与验收任务书</strong>
          <p>把任务模板、模式、参考目标、验收口径和交付物固定成一条任务链，避免结果只剩下几张表。</p>
          <div class="summary-grid" style="margin-top:10px">
            <div class="summary-card"><span>任务模板</span><strong>${esc(missionFlowLabel)}</strong></div>
            <div class="summary-card"><span>任务模式</span><strong>${esc(builderMode.value)}</strong></div>
            <div class="summary-card"><span>参考目标</span><strong>${esc(builderReference.value || 'body_zero')}</strong></div>
            <div class="summary-card"><span>验收模板</span><strong>${esc(acceptanceConfig.label)}</strong></div>
          </div>
          <div class="flow-sequence" style="margin-top:12px">
            <div class="flow-node accent">
              <small>1. 任务语义</small>
              <strong>${esc(missionFlowLabel)}</strong>
              <p>${esc(builderMissionHelp.textContent || '先定义任务流程。')}</p>
            </div>
            <div class="flow-node">
              <small>2. 模式与参考</small>
              <strong>${esc(builderMode.value)} + ${esc(builderReference.value || 'body_zero')}</strong>
              <p>${esc(builderModeHelp.textContent || '再固定模式与参考目标。')}</p>
            </div>
            <div class="flow-node warm">
              <small>3. 验收口径</small>
              <strong>${esc(acceptanceConfig.label)}</strong>
              <p>${esc(`末端 ${fmt(acceptFinal)} deg / RMS ${fmt(acceptRms)} deg / 力矩 ${fmt(acceptTorque)} Nm`)}</p>
            </div>
            <div class="flow-node ok">
              <small>4. 交付物</small>
              <strong>${esc(protocol?.outputs || 'README / index / summary_metrics / dashboard')}</strong>
              <p>${esc(nextProtocol ? `后续继续进入 ${nextProtocol.label}。` : '创建后直接进入结果总览继续读图和回放。')}</p>
            </div>
          </div>
          <div class="detail-grid" style="margin-top:12px">
            <div class="detail-box"><strong>任务说明</strong><div>${esc(modeExplanation(builderMode.value))}</div></div>
            <div class="detail-box"><strong>推荐验收口径</strong><div>${esc(standardMeta.acceptance)}</div></div>
            <div class="detail-box"><strong>输出目录</strong><div>${esc(suggestedOutput)}</div></div>
            <div class="detail-box"><strong>下一协议</strong><div>${esc(nextProtocol ? `${nextProtocol.label}：${nextProtocol.summary}` : '当前协议已收束，适合转入结果总览。')}</div></div>
          </div>
          ${standardAssetBindingHtml(assetBinding, {
            title: '任务阶段主线资产链',
            showToolbar: false,
          })}
        `;
      }

      if (builderReviewBrief) {
        builderReviewBrief.innerHTML = `
          <strong>创建后阅读路径</strong>
          <p>创建实验后不要停在 JSON。平台默认阅读路径是“结果总览 -> 当前结果 -> 图表 / 对比 / 回放 -> 下一协议”。</p>
          <div class="flow-sequence" style="margin-top:12px">
            <div class="flow-node accent">
              <small>1. 创建计划</small>
              <strong>${esc(templateConfig?.label || '当前模板')}</strong>
              <p>${esc(description || `基于 ${scenarioName} 固化当前实验契约。`)}</p>
            </div>
            <div class="flow-node">
              <small>2. 运行结果</small>
              <strong>${esc(runCount)} 个 run</strong>
              <p>${esc(`结果会写入 ${suggestedOutput}，并生成 README、index、summary_metrics 和 dashboard。`)}</p>
            </div>
            <div class="flow-node warm">
              <small>3. 首屏判断</small>
              <strong>先看结果总览</strong>
              <p>先锁定代表结果、风险结果和当前结果，再决定进入对比或回放。</p>
            </div>
            <div class="flow-node ok">
              <small>4. 主线推进</small>
              <strong>${esc(nextProtocol?.label || '整理代表结果')}</strong>
              <p>${esc(nextProtocol?.summary || protocolDecision)}</p>
            </div>
          </div>
          <div class="detail-grid" style="margin-top:12px">
            <div class="detail-box"><strong>推荐结果入口</strong><div>结果总览 -> 当前结果 -> 图表 / 回放</div></div>
            <div class="detail-box"><strong>推荐结果图包</strong><div>${esc(standardMeta.figurePackage)}</div></div>
            <div class="detail-box"><strong>代表实验参考</strong><div>${esc(representativeConfig?.label || templateConfig?.label || '当前模板')}</div></div>
            <div class="detail-box"><strong>后续主线</strong><div>${esc(nextProtocol ? `${nextProtocol.label}：${nextProtocol.summary}` : '当前先形成代表计划和代表结果，再决定下一轮派生。')}</div></div>
          </div>
          ${standardAssetBindingHtml(assetBinding, {
            title: '创建后主线资产链',
            showToolbar: false,
          })}
          <div class="toolbar" style="margin-top:10px">
            <button type="button" onclick="${representativeId ? `focusExperimentLibrary('${esc(representativeId)}')` : 'void(0)'}" ${representativeId ? '' : 'disabled'}>回实验库看主线</button>
            <button class="secondary" type="button" onclick="${representativePlanPath ? `showExperiment('${esc(representativePlanPath)}')` : 'void(0)'}" ${representativePlanPath ? '' : 'disabled'}>打开代表计划</button>
            <button class="secondary" type="button" onclick="${representativeDashboard ? `showDashboard('${esc(representativeDashboard.path)}')` : 'void(0)'}" ${representativeDashboard ? '' : 'disabled'}>查看代表结果</button>
            <button class="secondary" type="button" onclick="${nextProtocolExperimentId ? `previewRoadmapExperiment('${esc(nextProtocolExperimentId)}')` : 'void(0)'}" ${nextProtocolExperimentId ? '' : 'disabled'}>定位下一协议实验</button>
          </div>
        `;
      }
    }

    function renderBuilderWorkbenchSummary() {
      if (!builderWorkbenchSummary) return;
      const snapshot = builderSnapshot();
      const {
        mission,
        presetConfig,
        secondPresetConfig,
        acceptanceConfig,
        sweepValues,
        secondSweepValues,
        secondSweepEnabled,
        mcSamples,
        runCount,
        scenario,
        scenarioName,
        description,
        acceptFinal,
        acceptRms,
        acceptTorque,
        templateConfig,
        protocol,
        nextProtocol,
        stage,
        stageLabel,
        suggestedOutput,
      } = snapshot;
      const {
        representativeId,
        representativeConfig,
        representativePlanPath,
        representativeDashboard,
        representativeAsset,
        academicMeta,
        standardMeta,
        assetBinding,
        nextProtocolExperimentId,
        primaryBranch,
      } = builderProtocolBundle(snapshot);
      const activeView = state.builderWorkbenchView || 'focus';
      const questionText = description || '当前还没有填写实验说明。建议先用一句话写清楚这组实验到底想比较什么。';
      const nextActionText = {
        question: '先锁定实验主线和模板，再进入变量设计。',
        variables: '确认扫描值和 run 规模是否过大，再进入任务与验收。',
        runtime: '确认 mission、mode 和验收口径后，进入预览与生成。',
        review: '现在可以创建计划，或直接创建并运行形成第一版结果资产。',
      }[stage] || '先锁定研究问题，再逐步补变量、任务和结果出口。';
      builderWorkbenchToggle?.querySelectorAll('[data-builder-workbench-view]').forEach(button => {
        button.classList.toggle('active', button.dataset.builderWorkbenchView === activeView);
      });
      if (builderWorkbenchSummaryCopy) {
        builderWorkbenchSummaryCopy.textContent = builderWorkbenchSummaryText(activeView);
      }
      if (activeView === 'scale') {
        builderWorkbenchSummary.innerHTML = `
          <div class="summary-grid">
            <div class="summary-card"><span>总 run 数</span><strong>${esc(runCount)}</strong></div>
            <div class="summary-card"><span>主变量取值</span><strong>${esc(sweepValues.length || 1)}</strong></div>
            <div class="summary-card"><span>第二变量</span><strong>${esc(secondSweepEnabled ? secondSweepValues.length : 0)}</strong></div>
            <div class="summary-card"><span>Monte Carlo</span><strong>${esc(mcSamples || 0)}</strong></div>
          </div>
          <div class="detail-grid" style="margin-top:12px">
            <div class="detail-box"><strong>主扫描变量</strong><div>${esc(presetConfig.label || '单场景')}</div></div>
            <div class="detail-box"><strong>第二扫描变量</strong><div>${esc(secondSweepEnabled ? (secondPresetConfig.label || '第二维已启用') : '未启用')}</div></div>
            <div class="detail-box"><strong>任务流程</strong><div>${esc(mission === 'detumble_then_hold' ? '消旋后保持' : '单模式保持')}</div></div>
            <div class="detail-box"><strong>验收模板</strong><div>${esc(acceptanceConfig.label)}</div></div>
          </div>
          <div class="callout" style="margin-top:12px; margin-bottom:0">
            <strong>规模判断</strong>
            <p>${esc(runCount === 1
              ? '当前仍是单场景单 run，适合先做闭环验证或最小演示。'
              : runCount <= 9
                ? `当前规模为 ${runCount} 个 run，适合快速比较变量差异并保持结果可读。`
                : `当前规模为 ${runCount} 个 run，已经进入批量实验；建议确认输出目录、验收模板和图表阅读路径。`)}</p>
            <p style="margin-top:8px">${esc(`末端误差阈值 ${fmt(acceptFinal)} deg，RMS 阈值 ${fmt(acceptRms)} deg，峰值力矩阈值 ${fmt(acceptTorque)} Nm。`)}</p>
          </div>
        `;
        return;
      }
      if (activeView === 'deliverables') {
        builderWorkbenchSummary.innerHTML = `
          <div class="summary-grid">
            <div class="summary-card"><span>输出目录</span><strong title="${esc(suggestedOutput)}">${esc(suggestedOutput)}</strong></div>
            <div class="summary-card"><span>根目录产物</span><strong>README / index / dashboard</strong></div>
            <div class="summary-card"><span>当前步骤</span><strong>${esc(stageLabel)}</strong></div>
            <div class="summary-card"><span>下一协议</span><strong>${esc(nextProtocol?.label || '结果复查')}</strong></div>
          </div>
          <div class="flow-sequence" style="margin-top:12px">
            <div class="flow-node info">
              <small>1. 生成计划</small>
              <strong>固定实验契约</strong>
              <p>先把场景、变量、任务和验收写成计划，避免实验只停留在临时字段组合。</p>
            </div>
            <div class="flow-node">
              <small>2. 运行结果</small>
              <strong>形成标准目录</strong>
              <p>运行后会落盘 README.md、index.json、summary_metrics.csv 和 dashboard.html。</p>
            </div>
            <div class="flow-node success">
              <small>3. 读图归档</small>
              <strong>进入结果工作台</strong>
              <p>优先去“结果总览”看摘要、风险和主线导航，再决定是否进入对比、回放和预览。</p>
            </div>
          </div>
          <div class="detail-grid" style="margin-top:12px">
            <div class="detail-box"><strong>当前出口</strong><div>${esc(stage === 'review' ? '可以直接创建或创建并运行。' : '先完成当前步骤，再到预览与生成统一确认。')}</div></div>
            <div class="detail-box"><strong>推荐结果入口</strong><div>结果总览 -> 当前结果 -> 图表与回放</div></div>
            <div class="detail-box"><strong>协议交付物</strong><div>${esc(protocol?.outputs || 'README / index / summary_metrics / dashboard')}</div></div>
            <div class="detail-box"><strong>后续主线</strong><div>${esc(nextProtocol ? `${nextProtocol.label}：${nextProtocol.summary}` : '当前协议已收束，适合整理代表结果或回到计划管理派生下一版。')}</div></div>
          </div>
          ${standardAssetBindingHtml(assetBinding, {
            title: '结果出口主线资产链',
            showToolbar: false,
          })}
          <div class="toolbar" style="margin-top:10px">
            <button type="button" onclick="${representativeId ? `focusExperimentLibrary('${esc(representativeId)}')` : 'void(0)'}" ${representativeId ? '' : 'disabled'}>回实验库看主线</button>
            <button class="secondary" type="button" onclick="${representativePlanPath ? `showExperiment('${esc(representativePlanPath)}')` : 'void(0)'}" ${representativePlanPath ? '' : 'disabled'}>打开代表计划</button>
            <button class="secondary" type="button" onclick="${representativeDashboard ? `showDashboard('${esc(representativeDashboard.path)}')` : 'void(0)'}" ${representativeDashboard ? '' : 'disabled'}>查看代表结果</button>
            <button class="secondary" type="button" onclick="${nextProtocolExperimentId ? `previewRoadmapExperiment('${esc(nextProtocolExperimentId)}')` : 'void(0)'}" ${nextProtocolExperimentId ? '' : 'disabled'}>定位下一协议实验</button>
          </div>
        `;
        return;
      }
      builderWorkbenchSummary.innerHTML = `
        <div class="summary-grid">
          <div class="summary-card"><span>当前场景</span><strong>${esc(scenarioName)}</strong></div>
          <div class="summary-card"><span>协议阶段</span><strong>${esc(protocol?.label || '待锁定')}</strong></div>
          <div class="summary-card"><span>当前步骤</span><strong>${esc(stageLabel)}</strong></div>
          <div class="summary-card"><span>下一动作</span><strong>${esc(nextActionText)}</strong></div>
        </div>
        <div class="callout info" style="margin-top:12px; margin-bottom:0">
          <strong>当前研究问题</strong>
          <p>${esc(questionText)}</p>
        </div>
        <div class="flow-sequence" style="margin-top:12px">
          <div class="flow-node accent">
            <small>1. 模板</small>
            <strong>${esc(templateConfig?.label || presetConfig.label || '当前模板')}</strong>
            <p>${esc(protocol?.summary || '先锁定研究主线和实验模板。')}</p>
          </div>
          <div class="flow-node">
            <small>2. 变量</small>
            <strong>${esc(presetConfig.label || '单场景')}</strong>
            <p>${esc(sweepValues.length ? `当前 ${sweepValues.length} 个取值。` : '当前先按单一配置运行。')}</p>
          </div>
          <div class="flow-node warm">
            <small>3. 任务</small>
            <strong>${esc(mission === 'detumble_then_hold' ? '消旋后保持' : '单模式保持')}</strong>
            <p>${esc(modeExplanation(builderMode.value))}</p>
          </div>
          <div class="flow-node ok">
            <small>4. 图包</small>
            <strong>${esc(standardMeta.figurePackage)}</strong>
            <p>${esc(academicMeta.takeaway || nextActionText)}</p>
          </div>
        </div>
        <div class="detail-grid" style="margin-top:12px">
          <div class="detail-box"><strong>实验主线</strong><div>${esc(templateConfig?.label || presetConfig.label || '当前模板')}</div></div>
          <div class="detail-box"><strong>场景定位</strong><div>${esc(scenario ? `${scenario.name} · ${scenario.system || '当前系统'} / ${scenario.controller || '当前控制器'}` : '先选择基线场景。')}</div></div>
          <div class="detail-box"><strong>协议说明</strong><div>${esc(protocol?.summary || '当前实验还没有完全落到标准协议上。')}</div></div>
          <div class="detail-box"><strong>下一协议</strong><div>${esc(nextProtocol?.label || '结果复查')}</div></div>
          <div class="detail-box"><strong>推荐结果图包</strong><div>${esc(standardMeta.figurePackage)}</div></div>
          <div class="detail-box"><strong>代表实验参考</strong><div>${esc(representativeConfig?.label || templateConfig?.label || '当前模板')}</div></div>
          <div class="detail-box"><strong>代表计划</strong><div>${esc(representativeAsset.plan ? `${representativeAsset.plan.name} · ${experimentScaleText(representativeAsset.plan)}` : '当前协议还没有稳定代表计划。')}</div></div>
          <div class="detail-box"><strong>代表结果</strong><div>${esc(representativeAsset.dashboard ? `${representativeAsset.dashboard.name} · ${representativeDashboardSummary(representativeAsset.dashboard)}` : '当前协议还没有稳定代表结果。')}</div></div>
        </div>
        ${standardAssetBindingHtml(assetBinding, {
          title: '设计焦点主线资产链',
          showToolbar: false,
        })}
        <div class="toolbar" style="margin-top:10px">
          <button type="button" onclick="${representativeId ? `focusExperimentLibrary('${esc(representativeId)}')` : 'void(0)'}" ${representativeId ? '' : 'disabled'}>回实验库看主线</button>
          <button class="secondary" type="button" onclick="${representativePlanPath ? `showExperiment('${esc(representativePlanPath)}')` : 'void(0)'}" ${representativePlanPath ? '' : 'disabled'}>打开代表计划</button>
          <button class="secondary" type="button" onclick="${representativeDashboard ? `showDashboard('${esc(representativeDashboard.path)}')` : 'void(0)'}" ${representativeDashboard ? '' : 'disabled'}>查看代表结果</button>
          <button class="secondary" type="button" onclick="${primaryBranch ? `previewRoadmapExperiment('${esc(primaryBranch.experimentId)}')` : 'void(0)'}" ${primaryBranch ? '' : 'disabled'}>定位关键支线</button>
        </div>
      `;
    }

    function updateBuilderHints() {
      const {
        mission,
        preset,
        presetConfig,
        secondPreset,
        secondPresetConfig,
        acceptancePreset,
        acceptanceConfig,
        sweepValues,
        secondSweepValues,
        secondSweepEnabled,
        mcSamples,
        runCount,
        scenario,
        scenarioName,
        description,
        acceptFinal,
        acceptRms,
        acceptTorque,
        templateConfig,
        protocol,
        nextProtocol,
        suggestedOutput,
      } = builderSnapshot();
      const {
        representativeConfig,
        representativeAsset,
        nextProtocolExperimentId,
      } = builderProtocolBundle(builderSnapshot());

      builderScenarioHelp.textContent = scenario
        ? `当前场景为 ${scenario.name}，时长 ${fmt(scenario.duration_s)} s，步长 ${fmt(scenario.dt_s)} s，系统 ${scenario.system || '—'}，控制器 ${scenario.controller || '—'}。`
        : '选择实验基线场景。它决定动力学、控制器、环境、初始状态和默认输出设置。';
      builderSelectedScenario.textContent = scenario
        ? `${scenario.name} 适合从 ${scenario.system || '当前系统'} / ${scenario.controller || '当前控制器'} 出发做基线实验；当前场景时长 ${fmt(scenario.duration_s)} s，便于快速演示和回归。`
        : '选择场景后，这里会说明它适合做什么实验。';

      if (mission === 'detumble_then_hold') {
        builderMissionHelp.textContent = '当前任务会先执行 detumble，再切换到目标保持模式，适合展示模式切换、收敛过程和时间线联动。';
        builderModeHelp.textContent = `这里选择 detumble 结束后进入的保持模式。${modeExplanation(builderMode.value)}`;
        builderDetumbleLabel.hidden = false;
      } else {
        builderMissionHelp.textContent = '当前任务会从仿真开始一直保持同一模式，适合做稳态性能验证、参数扫描和控制器对比。';
        builderModeHelp.textContent = `这里选择整个实验期间维持的模式。${modeExplanation(builderMode.value)}`;
        builderDetumbleLabel.hidden = true;
      }

      builderPresetHelp.textContent = presetConfig.help;
      builderSweepHelp.textContent = inferSweepMeaning(builderSweepPath.value);
      builderSecondPresetHelp.textContent = secondPreset === 'custom'
        ? '需要二维实验时启用第二扫描变量。它会和第一变量做笛卡尔组合，生成更完整的权衡实验。'
        : secondPresetConfig.help;
      builderSecondSweepHelp.textContent = secondSweepEnabled || String(builderSecondSweepPath.value || '').trim()
        ? inferSweepMeaning(builderSecondSweepPath.value)
        : '第二维扫描的配置路径。常用于环境扰动与执行器能力的双变量实验。';
      const activeValuePreset = builderSweepValuesPreset.value;
      const activePresetHelp = presetConfig.valuePresets.find(item => item.values === activeValuePreset)?.help;
      builderValuesPresetHelp.textContent = activePresetHelp || '选择模板后会自动填充一组推荐扫描值，你也可以继续手动修改。';
      const activeSecondValuePreset = builderSecondSweepValuesPreset.value;
      const activeSecondPresetHelp = secondPresetConfig.valuePresets.find(item => item.values === activeSecondValuePreset)?.help;
      builderSecondValuesPresetHelp.textContent = activeSecondPresetHelp || '选择模板后会自动填充第二维推荐取值，也可以继续手动修改。';
      builderValuesHelp.textContent = sweepValues.length
        ? `当前已选择 ${sweepValues.length} 个扫描值：${sweepValues.join('、')}。每个值都会生成一组实验分支。`
        : '参数扫描的候选值列表，用逗号分隔。每个值会生成一组实验分支。';
      builderSecondValuesHelp.textContent = secondSweepEnabled
        ? `第二维已选择 ${secondSweepValues.length} 个扫描值：${secondSweepValues.join('、')}。它会和第一维组合展开。`
        : '第二维参数扫描的候选值列表。留空时不启用第二扫描变量。';
      builderAcceptanceHelp.textContent = acceptancePreset === 'custom'
        ? '当前使用自定义验收规则，平台会按你填写的阈值判断通过/失败。'
        : acceptanceConfig.help;
      builderOutputHelp.textContent = builderOutput.value.trim()
        ? '当前会把结果写入你手动指定的输出目录。'
        : '当前未手动填写输出目录，平台会按默认规则自动生成结果目录。';
      builderOutputPreview.textContent = `输出目录预览：${suggestedOutput}`;
      builderSummaryCards.innerHTML = `
        <div class="summary-card"><span>扫描变量</span><strong>${esc(presetConfig.label || '单场景')}</strong></div>
        <div class="summary-card"><span>取值数量</span><strong>${sweepValues.length || 1}</strong></div>
        <div class="summary-card"><span>第二变量</span><strong>${secondSweepEnabled ? esc(secondPresetConfig.label || builderSecondSweepPath.value) : '未启用'}</strong></div>
        <div class="summary-card"><span>任务流程</span><strong>${mission === 'detumble_then_hold' ? '消旋后保持' : '单模式保持'}</strong></div>
        <div class="summary-card"><span>验收模板</span><strong>${esc(acceptanceConfig.label)}</strong></div>
        <div class="summary-card"><span>输出目录</span><strong title="${esc(suggestedOutput)}">${esc(suggestedOutput)}</strong></div>
      `;

      const parts = [
        `当前计划会基于场景“${scenarioName}”生成 ${runCount} 个 run。`,
        sweepValues.length ? `参数扫描维度 ${sweepValues.length} 个取值。` : '未填写扫描取值时，按单一参数配置运行。',
        secondSweepEnabled ? `第二扫描维度 ${secondSweepValues.length} 个取值，会与第一维做组合。` : '未启用第二扫描变量。',
        mcSamples > 0 ? `Monte Carlo 会重复 ${mcSamples} 次。` : '未启用 Monte Carlo。',
        `任务模板为“${mission === 'detumble_then_hold' ? '消旋后保持' : '单模式保持'}”。`,
        `验收采用“${acceptanceConfig.label}”，末端误差阈值 ${fmt(acceptFinal)} deg，RMS 阈值 ${fmt(acceptRms)} deg，峰值力矩阈值 ${fmt(acceptTorque)} Nm。`,
      ];
      if (builderReference.value.trim()) {
        parts.push(`参考目标为 ${builderReference.value.trim()}。`);
      }
      builderPreview.textContent = parts.join(' ');
      const secondLabel = secondSweepEnabled ? ` × ${secondPresetConfig.label || builderSecondSweepPath.value}` : '';
      builderSelectedExperiment.textContent = `${presetConfig.label || '当前变量'}${secondLabel} + ${mission === 'detumble_then_hold' ? '消旋后保持' : '单模式保持'} + ${builderMode.value}，当前更适合用来${mission === 'detumble_then_hold' ? '展示模式切换和收敛过程' : secondSweepEnabled ? '做双变量权衡分析' : '比较稳态性能和参数差异'}。${description ? ` 实验意图：${description}` : ''}${representativeConfig ? ` 当前代表实验参考为 ${representativeConfig.label}。` : ''}`;
      if (builderSelectedProtocol) {
        builderSelectedProtocol.textContent = protocol
          ? `${protocol.label}：${protocol.summary} 当前更接近“${templateConfig?.label || presetConfig.label || '当前模板'}”这类入口实验。${representativeAsset.dashboard ? ` 这条主线已有代表结果 ${representativeAsset.dashboard.name}。` : representativeAsset.plan ? ` 这条主线已有代表计划 ${representativeAsset.plan.name}，还可以继续补代表结果。` : ' 这条主线还没有稳定代表资产，适合先生成第一版计划。'}${nextProtocolExperimentId ? ' 完成后可继续接下一协议实验。' : ''}`
          : '选择模板后，这里会把当前实验放回一条标准研究协议里，而不只是显示零散字段。';
      }
      if (builderProtocolWorkflow) {
        builderProtocolWorkflow.textContent = protocol
          ? `推荐顺序：${protocolStepsText(protocol)}。建议先产出 ${protocol.outputs}，再进入${nextProtocol?.label || '结果复查'}。`
          : '选择模板后，这里会给出推荐步骤、结果产物和下一条实验链路。';
      }
      renderBuilderProtocolSheet();
      renderBuilderVisualCanvas();
      renderBuilderWorkbenchSummary();
      renderBuilderStageBriefs(builderSnapshot());
      const referenceMeaning = {
        body_zero: '机体零姿态，适合做基线稳定控制验证。',
        sun: '太阳参考，适合展示跟踪太阳方向的任务模式。',
        nadir: '对地参考，适合对地指向或载荷任务演示。',
      };
      builderReferenceHelp.textContent = referenceMeaning[builderReference.value] || '姿态参考定义，决定控制器要跟踪的目标姿态。';
      highlightExperimentTemplate();
    }

    function renderSweepValuePresets() {
      const presetConfig = sweepPresetConfig(builderSweepPreset.value || 'custom');
      const options = [`<option value="custom">手动填写</option>`];
      for (const item of presetConfig.valuePresets) {
        options.push(`<option value="${esc(item.values)}">${esc(item.label)} · ${esc(item.values)}</option>`);
      }
      builderSweepValuesPreset.innerHTML = options.join('');
      if (presetConfig.valuePresets.length) {
        const currentValues = String(builderSweepValues.value || '').trim();
        const matching = presetConfig.valuePresets.find(item => item.values === currentValues);
        if (matching) {
          builderSweepValuesPreset.value = matching.values;
        } else {
          builderSweepValuesPreset.value = presetConfig.valuePresets[0].values;
          if (!currentValues) {
            builderSweepValues.value = presetConfig.valuePresets[0].values;
          }
        }
      } else {
        builderSweepValuesPreset.value = 'custom';
      }
    }

    function renderSecondSweepValuePresets() {
      const presetConfig = sweepPresetConfig(builderSecondSweepPreset.value || 'custom');
      const options = [`<option value="custom">手动填写</option>`];
      for (const item of presetConfig.valuePresets) {
        options.push(`<option value="${esc(item.values)}">${esc(item.label)} · ${esc(item.values)}</option>`);
      }
      builderSecondSweepValuesPreset.innerHTML = options.join('');
      if (presetConfig.valuePresets.length) {
        const currentValues = String(builderSecondSweepValues.value || '').trim();
        const matching = presetConfig.valuePresets.find(item => item.values === currentValues);
        if (matching) {
          builderSecondSweepValuesPreset.value = matching.values;
        } else {
          builderSecondSweepValuesPreset.value = presetConfig.valuePresets[0].values;
          if (!currentValues) {
            builderSecondSweepValues.value = presetConfig.valuePresets[0].values;
          }
        }
      } else {
        builderSecondSweepValuesPreset.value = 'custom';
      }
    }

    function highlightExperimentTemplate() {
      if (!builderTemplateGrid) return;
      const templateId = currentBuilderTemplateId();
      builderTemplateGrid.querySelectorAll('.template-card').forEach(card => {
        card.classList.toggle('active', card.dataset.template === templateId);
      });
    }

    function applyAcceptancePreset(preset, {force = false} = {}) {
      const config = acceptancePresetConfig(preset || builderAcceptancePreset.value || 'standard_hold');
      builderAcceptancePreset.value = preset || builderAcceptancePreset.value || 'standard_hold';
      if (config.thresholds && (force || builderAcceptancePreset.value !== 'custom')) {
        builderAcceptFinal.value = String(config.thresholds.final);
        builderAcceptRms.value = String(config.thresholds.rms);
        builderAcceptTorque.value = String(config.thresholds.torque);
      }
      builderAcceptanceHelp.textContent = config.help;
    }

    function applyExperimentTemplate(templateId) {
      const config = experimentTemplateConfig(templateId);
      if (!config) return;
      const scenario = state.workspace?.scenarios?.find(item => item.path === builderScenario.value);
      state.builderCategory = builderTemplateCategoryId(templateId);
      renderBuilderTemplateBrowser();
      builderSweepPreset.value = config.sweepPreset;
      builderSweepPath.value = config.sweepPreset;
      renderSweepValuePresets();
      builderSweepValues.value = config.sweepValues;
      builderSweepValuesPreset.value = config.sweepValues;
      builderSecondSweepPreset.value = config.secondSweepPreset || 'custom';
      builderSecondSweepPath.value = config.secondSweepPreset || '';
      renderSecondSweepValuePresets();
      builderSecondSweepValues.value = config.secondSweepValues || '';
      builderSecondSweepValuesPreset.value = config.secondSweepValues || 'custom';
      builderMission.value = config.mission;
      builderMode.value = config.mode;
      builderReference.value = config.reference;
      builderAcceptancePreset.value = config.acceptancePreset || 'standard_hold';
      applyAcceptancePreset(builderAcceptancePreset.value, {force: true});
      builderMcSamples.value = String(config.mcSamples);
      builderMcSeed.value = config.mcSeed === '' ? '' : String(config.mcSeed);
      builderDetumble.value = String(config.detumbleS);
      if (!builderName.value || builderName.value.endsWith('_experiment') || builderName.value.includes('platform_smoke')) {
        const prefix = (scenario?.name || 'experiment').replace(/\\s+/g, '_');
        builderName.value = `${prefix}_${config.nameSuffix}`;
      }
      updateBuilderHints();
      highlightExperimentTemplate();
    }

    async function prepareQuickExample(demoId) {
      const config = quickDemoConfig(demoId);
      if (!config) return null;
      const scenarioPath = resolveQuickDemoScenario(config);
      if (!scenarioPath) {
        setStatus(`示例 ${config.label} 需要的场景模板当前不在工作区中。`, 'bad');
        return null;
      }
      builderScenario.value = scenarioPath;
      await showScenario(scenarioPath, true);
      applyExperimentTemplate(config.templateId);
      builderName.value = config.planName;
      builderOutput.value = `results/platform_ui/${config.planName}`;
      updateBuilderHints();
      renderBuilderViewMode();
      state.builderStage = 'review';
      renderBuilderStage();
      state.quickDemoSession = {
        demoId,
        label: config.label,
        description: config.description,
        status: 'prepared',
        outputRoot: builderOutput.value,
      };
      renderQuickDemoStatus();
      focusBuilderSection();
      pushActivity('已载入示例模板', `${config.label} · 场景 ${scenarioPath} · 模板 ${config.templateId}`, 'scenario');
      setStatus(`已载入示例：${config.label}。可以直接创建并运行，或先微调参数。`, 'ok');
      return config;
    }

    async function prepareCuratedExperiment(experimentId) {
      const config = curatedExperimentConfig(experimentId);
      if (!config) return null;
      const scenarioPath = resolveCuratedExperimentScenario(config);
      if (!scenarioPath) {
        setStatus(`推荐实验 ${config.label} 需要的场景模板当前不在工作区中。`, 'bad');
        return null;
      }
      builderScenario.value = scenarioPath;
      await showScenario(scenarioPath, true);
      applyExperimentTemplate(config.templateId);
      builderName.value = config.planName;
      builderOutput.value = `results/platform_library/${config.planName}`;
      updateBuilderHints();
      renderBuilderViewMode();
      state.builderStage = 'review';
      renderBuilderStage();
      focusBuilderSection();
      pushActivity('已载入推荐实验', `${config.label} · 场景 ${scenarioPath} · 模板 ${config.templateId}`, 'experiment');
      setStatus(`已载入推荐实验：${config.label}。可以继续微调变量、验收门限和输出目录。`, 'ok');
      return config;
    }

    async function runQuickExample(demoId) {
      const config = await prepareQuickExample(demoId);
      if (!config) return null;
      state.quickDemoSession = {
        ...(state.quickDemoSession || {}),
        demoId,
        label: config.label,
        description: config.description,
        status: 'creating',
        outputRoot: builderOutput.value,
      };
      renderQuickDemoStatus();
      setStatus(`正在启动示例：${config.label}...`);
      return createPlan({runAfterCreate: true, quickDemo: config});
    }

    function renderExperimentEditor(plan = null) {
      state.currentExperiment = plan ? plan.path : null;
      state.currentExperimentDirty = false;
      state.currentExperimentSummary = plan ? `run ${experimentRunCount(plan)} / sweep ${plan.sweeps} / MC ${plan.monte_carlo_samples}` : '未选择';
      state.currentExperimentMapping = plan?.mapping || null;
      editorText.value = plan?.text || '';
      editorText.disabled = !plan;
      editorLoad.disabled = !plan;
      editorSave.disabled = !plan;
      editorValidate.disabled = !plan;
      editorRun.disabled = !plan;
      editorDuplicate.disabled = !plan;
      editorRename.disabled = !plan;
      editorArchive.disabled = !plan;
      editorTitle.textContent = plan?.name || '还没有载入实验计划';
      editorPath.textContent = plan?.path || '从左侧实验计划列表选择“编辑”。';
      editorSummary.textContent = state.currentExperimentSummary;
      renderEditorPlanContext(plan);
      editorOverview.innerHTML = plan ? editorOverviewHtml(plan.mapping, plan) : '载入实验计划后，这里会显示结构化概览。';
      if (!plan) {
        state.editorWorkbenchView = 'context';
        state.editorViewMode = 'overview';
      }
      updateEditorButtons();
      renderEditorWorkbenchView();
      renderEditorViewMode();
      if (state.workspace?.experiments) {
        renderExperimentPicker();
        renderExperimentListSwitcher();
        renderEditorQuickLoad();
        renderRecentExperimentPlans();
        renderManageWorkbenchSummary();
      }
    }

    function updateEditorButtons() {
      const active = Boolean(state.currentExperiment);
      editorLoad.disabled = !active;
      editorSave.disabled = !active;
      editorValidate.disabled = !active;
      editorRun.disabled = !active;
      editorDuplicate.disabled = !active;
      editorRename.disabled = !active;
      editorArchive.disabled = !active;
      if (active) {
        editorSummary.textContent = state.currentExperimentDirty
          ? `${state.currentExperimentSummary} · 已修改`
          : state.currentExperimentSummary;
      }
    }

    function renderScenarios() {
      const rows = state.workspace.scenarios;
      if (!rows.length) {
        document.getElementById('scenarios').innerHTML = '<div class="empty">scenarios/ 目录下还没有普通场景文件。</div>';
        return;
      }
      document.getElementById('scenarios').innerHTML = `<table><thead><tr><th>名称</th><th>时间</th><th>系统</th><th>操作</th></tr></thead><tbody>${rows.map(item => {
        const timing = `${item.duration_s ?? ''} s / dt ${item.dt_s ?? ''}`;
        const system = `${item.system || ''} / ${item.controller || ''}`;
        return `<tr><td title="${esc(item.path)}">${esc(item.name)}</td><td title="${esc(timing)}">${esc(timing)}</td><td title="${esc(system)}">${esc(system)}</td><td><button onclick="showScenario('${esc(item.path)}')">详情</button> <button onclick="validateScenario('${esc(item.path)}')">校验</button></td></tr>`;
      }).join('')}</tbody></table>`;
    }

    function renderExperiments() {
      const rows = filteredExperiments();
      const selected = new Set(state.selectedExperiments || []);
      const allSelected = rows.length > 0 && rows.every(plan => selected.has(plan.path));
      if (!rows.length) {
        const emptyText = (state.experimentFilter.trim() || state.experimentStatusFilter !== 'all')
          ? '当前筛选条件下没有匹配的实验计划。可以清空筛选、切换状态或换个关键词。'
          : 'scenarios/ 目录下还没有实验计划。';
        document.getElementById('experiments').innerHTML = `<div class="empty">${emptyText}</div>`;
        renderManagePoolCockpit();
        renderManageWorkbenchSummary();
        return;
      }
      document.getElementById('experiments').innerHTML = `<table><thead><tr><th><input type="checkbox" aria-label="select all experiments" ${allSelected ? 'checked' : ''} onchange="${allSelected ? 'clearSelectedExperiments()' : 'selectAllFilteredExperiments()'}"></th><th>名称</th><th>实验主线</th><th>资产状态</th><th>路径</th><th>运行规模</th><th>最近结果</th><th>操作</th></tr></thead><tbody>${rows.map(plan => {
        const runs = plan.error ? plan.error : `${(plan.sweeps || 0) ? '参数扫描' : '单场景'} / MC ${plan.monte_carlo_samples || 0}`;
        const dashboard = matchingDashboardForPlan(plan);
        const resultText = dashboard ? `${dashboard.run_count} run · ${dashboard.updated_at || '最近生成'}` : '暂无结果';
        const profile = planAssetProfile(plan);
        return `<tr><td><input type="checkbox" aria-label="select experiment ${esc(plan.name)}" ${selected.has(plan.path) ? 'checked' : ''} onchange="toggleExperimentSelection('${esc(plan.path)}', this.checked)"></td><td title="${esc(plan.name)}">${esc(plan.name)}</td><td title="${esc(profile.line)}">${esc(profile.line)}</td><td title="${esc(profile.status)}">${esc(profile.status)}</td><td title="${esc(plan.path)}">${esc(plan.path)}</td><td title="${esc(runs)}">${esc(runs)}</td><td title="${esc(resultText)}">${esc(resultText)}</td><td><button onclick="showExperiment('${esc(plan.path)}')">编辑</button> <button onclick="duplicateExperiment('${esc(plan.path)}')">复制</button> <button onclick="renameExperiment('${esc(plan.path)}')">重命名</button> <button onclick="archiveExperiment('${esc(plan.path)}')">归档</button> <button onclick="validatePlan('${esc(plan.path)}')">校验</button> <button class="primary" onclick="runPlan('${esc(plan.path)}')">运行</button> <button class="secondary" onclick="${dashboard ? `showDashboard('${esc(dashboard.path)}')` : 'void(0)'}" ${dashboard ? '' : 'disabled'}>结果</button></td></tr>`;
      }).join('')}</tbody></table>`;
      renderManagePoolCockpit();
      renderManageWorkbenchSummary();
    }

    function renderDashboards() {
      const allRows = [...state.workspace.dashboards].sort((a, b) => Number(b.updated_ts || 0) - Number(a.updated_ts || 0));
      const filterText = state.dashboardFilter.trim().toLowerCase();
      const rows = allRows.filter(item => {
        const textOk = !filterText || `${item.name || ''} ${item.scenario || ''} ${item.path || ''}`.toLowerCase().includes(filterText);
        if (!textOk) return false;
        if (state.dashboardStatusFilter === 'latest' && item.path !== state.latestRunDashboard) return false;
        if (state.dashboardStatusFilter === 'current' && item.path !== state.currentDashboard) return false;
        if (state.dashboardStatusFilter === 'accepted' && Number(item.acceptance_rate || 0) < 0.999) return false;
        if (state.dashboardStatusFilter === 'needs_attention' && Number(item.acceptance_rate || 0) >= 0.999) return false;
        if (state.dashboardLineFilter !== 'all' && resultLineForDashboard(item) !== state.dashboardLineFilter) return false;
        return true;
      });
      const lineEntries = groupedDashboardLines(allRows);
      if (dashboardLineFilter) {
        const activeLine = lineEntries.some(([name]) => name === state.dashboardLineFilter) ? state.dashboardLineFilter : 'all';
        state.dashboardLineFilter = activeLine;
        dashboardLineFilter.innerHTML = `<option value="all">全部主线</option>${lineEntries.map(([name, count]) => `<option value="${esc(name)}"${name === activeLine ? ' selected' : ''}>${esc(name)} · ${count}</option>`).join('')}`;
      }
      const browserView = ['representative', 'map', 'risk', 'recent'].includes(state.resultsBrowserView) ? state.resultsBrowserView : 'representative';
      state.resultsBrowserView = browserView;
      resultsBrowserToggle?.querySelectorAll('[data-results-browser-view]').forEach(button => {
        button.classList.toggle('active', button.dataset.resultsBrowserView === browserView);
      });
      if (resultsBrowserSummary) {
        resultsBrowserSummary.textContent = resultsBrowserSummaryText(browserView);
      }
      const latest = allRows.find(item => item.path === state.latestRunDashboard);
      if (latest) {
        const latestRate = `${Math.round((latest.acceptance_rate || 0) * 1000) / 10}%`;
        latestDashboardBanner.innerHTML = `
          <div class="result-banner">
            <span>最新运行通知</span>
            <strong>${esc(latest.name)} 已完成，场景 ${esc(latest.scenario || '—')}，run ${esc(latest.run_count)}，通过率 ${esc(latestRate)}。</strong>
            <div class="toolbar" style="margin-top:10px">
              <button type="button" onclick="showDashboard('${esc(latest.path)}')">查看最新结果</button>
              <button class="secondary" type="button" onclick="window.open('${esc(latest.url)}', '_blank')">新窗口打开</button>
            </div>
          </div>
        `;
      } else {
        latestDashboardBanner.innerHTML = '';
      }
      const browserLines = groupedDashboardLines(rows).slice(0, 4);
      const representativeCards = browserLines.map(([groupName, count]) => {
        const asset = representativeAssetRecord({line: groupName, dashboards: rows});
        const representative = asset.dashboard || representativeDashboardForLine(groupName, rows);
        const best = bestDashboardForLine(groupName, rows);
        const active = representative?.path === state.currentDashboard || best?.path === state.currentDashboard;
        return `
          <button class="history-card ${active ? 'active' : ''}" type="button" onclick="${representative ? `showDashboard('${esc(representative.path)}')` : best ? `showDashboard('${esc(best.path)}')` : 'void(0)'}" ${(representative || best) ? '' : 'disabled'}>
            <span>${esc(active ? '当前入口' : (representative ? '代表结果' : asset.status))}</span>
            <strong>${esc(groupName)}</strong>
            <p>${esc(count)} 个结果 · ${esc(asset.plan ? `计划 ${asset.plan.name}` : '计划待固化')}<br>${esc((representative || best)?.name || '暂无代表结果')} · 通过率 ${esc(percentText((representative || best)?.acceptance_rate || 0))}</p>
          </button>
        `;
      }).join('');
      const riskRows = [...rows]
        .filter(item => Number(item.acceptance_rate || 0) < 0.999)
        .sort((a, b) => Number(a.acceptance_rate || 0) - Number(b.acceptance_rate || 0) || Number(b.failed_count || 0) - Number(a.failed_count || 0))
        .slice(0, 6);
      const riskCards = riskRows.map(item => `
        <button class="history-card" type="button" onclick="showDashboard('${esc(item.path)}')">
          <span>${esc(dashboardRiskLabel(item))}</span>
          <strong>${esc(item.name)}</strong>
          <p>${esc(resultLineForDashboard(item))} · 通过率 ${esc(percentText(item.acceptance_rate))}<br>失败 ${fmt(item.failed_count)} 个 run · ${esc(dashboardRiskReason(item))}</p>
        </button>
      `).join('');
      const recentRows = rows.slice(0, 6);
      const recentItems = recentRows.map(item => {
        const active = item.path === state.currentDashboard || item.path === state.latestRunDashboard;
        const badge = item.path === state.latestRunDashboard ? '最新运行' : item.path === state.currentDashboard ? '当前查看' : '最近结果';
        const rate = `${Math.round((item.acceptance_rate || 0) * 1000) / 10}%`;
        return `<button class="history-item ${active ? 'active' : ''}" type="button" onclick="showDashboard('${esc(item.path)}')"><span class="history-time">${esc(item.updated_at || '')}</span><div><strong>${esc(item.name)}</strong><p>${esc(item.scenario || '—')} · run ${esc(item.run_count)} · 通过率 ${esc(rate)}</p></div><span class="chip">${esc(badge)}</span></button>`;
      }).join('');
      const detail = browserPrimaryDashboard(browserView, rows, allRows, browserLines);
      const detailBinding = detail ? standardAssetBindingRecord({result: detail, dashboards: rows}) : null;
      const detailPlan = detailBinding?.plan || [...(state.workspace?.experiments || [])].find(plan => matchingDashboardForPlan(plan)?.path === detail?.path) || null;
      const detailSemanticMarkup = detail
        ? resultSemanticCardsHtml(detail, detailPlan, {
            dashboards: rows,
            briefTitle: browserView === 'recent' ? '最近结果实验说明卡' : '代表结果实验说明卡',
            briefLead: browserView === 'recent'
              ? '最近结果不只按时间看，也直接说明这次实验到底在回答什么问题。'
              : '代表结果浏览器里也直接给实验语义，避免只看到“主线卡”和结果文件名。',
            readingTitle: browserView === 'recent' ? '最近结果阅读任务书' : '代表结果阅读任务书',
            readingLead: browserView === 'recent'
              ? '刚跑完的结果值不值得继续细读，在这里先做第一轮判断。'
              : '先在浏览器层就把读图顺序和下一步判断固定下来。',
            bindingTitle: browserView === 'recent' ? '最近结果主线资产链' : '代表结果主线资产链',
            showToolbar: false,
          })
        : '';
      const protocolMapRecords = protocolWorkspaceMapRecords({
        dashboards: rows,
        plans: state.workspace?.experiments || [],
      });
      if (resultsBrowserCockpit) {
        if (!rows.length) {
          resultsBrowserCockpit.innerHTML = `
            <div class="callout" style="margin-bottom:12px">
              <strong>结果浏览驾驶舱</strong>
              <p>${esc(allRows.length ? '当前筛选条件下没有匹配的结果。可以清空筛选或切换状态。' : '还没有结果界面。运行一次实验后会自动生成。')}</p>
            </div>
          `;
        } else if (browserView === 'map') {
          const readyProtocolCount = protocolMapRecords.filter(record => record.readyResults > 0).length;
          const missingProtocolCount = protocolMapRecords.filter(record => record.readyResults === 0).length;
          const focusProtocolId = detailBinding?.protocolId || '';
          const focusProtocol = experimentProtocolConfig(focusProtocolId);
          resultsBrowserCockpit.innerHTML = `
            <div class="history-detail" style="margin-bottom:12px">
              <strong>结果地图浏览器</strong>
              <p>这里按协议阶段检查结果覆盖，先回答“哪条主线已经有稳定代表结果、哪条还缺结果入口”，再决定进入具体结果。</p>
              <div class="summary-grid" style="margin-top:10px">
                <div class="summary-card"><span>筛选结果</span><strong>${rows.length}</strong></div>
                <div class="summary-card"><span>协议阶段</span><strong>${protocolMapRecords.length}</strong></div>
                <div class="summary-card"><span>已有结果协议</span><strong>${readyProtocolCount}</strong></div>
                <div class="summary-card"><span>待补结果协议</span><strong>${missingProtocolCount}</strong></div>
                <div class="summary-card"><span>当前焦点协议</span><strong>${esc(focusProtocol?.label || '未映射')}</strong></div>
              </div>
            </div>
            <div class="intro-grid" style="margin-top:10px">
              ${protocolMapRecords.map(record => protocolWorkspaceCardHtml(record, {
                mode: 'results',
                focusProtocolId,
              })).join('')}
            </div>
            <div class="history-detail" style="margin-top:12px; margin-bottom:0">
              <strong>结果阅读建议</strong>
              <p>结果地图适合先锁定协议缺口，再决定是回代表结果、去最近结果，还是回计划管理补一轮运行。</p>
              <div class="detail-grid" style="margin-top:10px">
                <div class="detail-box"><strong>当前建议</strong><div>${esc(readyProtocolCount ? '优先打开已有代表结果的协议继续读图，再把缺口协议补成标准结果。' : '当前还没有形成稳定的协议结果入口，建议先从实验库或计划管理完成首轮标准运行。')}</div></div>
                <div class="detail-box"><strong>筛选口径</strong><div>${esc(state.dashboardLineFilter === 'all' ? '当前未限制主线，结果地图展示的是整个工作区的协议结果覆盖。' : `当前只看主线 ${state.dashboardLineFilter}，结果地图会反映这条主线相关结果。`)}</div></div>
              </div>
            </div>
          `;
        } else if (browserView === 'risk') {
          resultsBrowserCockpit.innerHTML = `
            <div class="history-detail" style="margin-bottom:12px">
              <strong>风险结果浏览器</strong>
              <p>这里优先保留需要先处理的实验。先打开通过率低、失败 run 多或边界解释不清楚的结果，再决定是否回到计划管理。</p>
              <div class="summary-grid" style="margin-top:10px">
                <div class="summary-card"><span>筛选结果</span><strong>${rows.length}</strong></div>
                <div class="summary-card"><span>风险结果</span><strong>${riskRows.length}</strong></div>
                <div class="summary-card"><span>当前主线</span><strong>${esc(state.dashboardLineFilter === 'all' ? '全部主线' : state.dashboardLineFilter)}</strong></div>
                <div class="summary-card"><span>推荐动作</span><strong>${esc(riskRows.length ? '先诊断' : '整体稳定')}</strong></div>
              </div>
            </div>
            <div class="history-grid" style="margin-top:10px">
              ${riskCards || '<div class="detail-box"><strong>当前状态</strong><div>当前筛选范围内没有高风险结果，可以切回代表结果或最近结果继续浏览。</div></div>'}
            </div>
          `;
        } else if (browserView === 'recent') {
          const detailRate = detail ? `${Math.round((detail.acceptance_rate || 0) * 1000) / 10}%` : '—';
          const detailRepresentative = detail ? isRepresentativeDashboard(detail, rows) : false;
          resultsBrowserCockpit.innerHTML = `
            <div class="history-detail" style="margin-bottom:12px">
              <strong>最近结果浏览器</strong>
              <p>这里按时间回看刚生成的实验，适合快速判断“刚跑完的结果值不值得继续细读或回到计划”。</p>
              <div class="summary-grid" style="margin-top:10px">
                <div class="summary-card"><span>最近结果</span><strong>${recentRows.length}</strong></div>
                <div class="summary-card"><span>当前查看</span><strong>${esc(detail?.name || '—')}</strong></div>
                <div class="summary-card"><span>通过率</span><strong>${esc(detailRate)}</strong></div>
                <div class="summary-card"><span>结果角色</span><strong>${esc(detail ? (detailRepresentative ? '最近运行也是代表结果' : '最近运行，仅供回看') : '等待结果')}</strong></div>
              </div>
            </div>
            ${detail ? `
              <div class="history-detail" style="margin-bottom:12px">
                <strong>${esc(detail.name)} · 最近结果详情</strong>
                <p>场景 ${esc(detail.scenario || '—')}，共 ${esc(detail.run_count)} 个 run，通过率 ${esc(detailRate)}，最佳 run 为 ${esc(detail.best_run_id || '—')}，最佳末端误差 ${fmt(detail.best_final_error_deg)} deg。${esc(detailRepresentative ? '这次最近运行也已经足够稳定，可继续作为主线代表结果。' : '这次最近运行主要用于回看刚跑完的实验，不自动等同于主线代表结果。')}</p>
                <div class="toolbar" style="margin-top:10px">
                  <button type="button" onclick="showDashboard('${esc(detail.path)}')">查看详情</button>
                  <button class="secondary" type="button" onclick="window.open('${esc(detail.url)}', '_blank')">新窗口打开</button>
                </div>
              </div>
              ${detailSemanticMarkup}
            ` : ''}
            <div class="history-timeline">
              <h3>最近结果时间线</h3>
              <div class="history-items">
                ${recentItems || '<div class="empty">当前没有最近结果。</div>'}
              </div>
            </div>
          `;
        } else {
          const representativeDetail = detail ? lineDashboardAssetMeta(resultLineForDashboard(detail), rows) : null;
          resultsBrowserCockpit.innerHTML = `
            <div class="history-detail" style="margin-bottom:12px">
              <strong>代表结果浏览器</strong>
              <p>先按实验主线锁定各条结果链当前最值得打开的一项，再进入完整结果表。这样浏览页会更像平台入口，而不是纯历史目录。</p>
              <div class="summary-grid" style="margin-top:10px">
                <div class="summary-card"><span>筛选结果</span><strong>${rows.length}</strong></div>
                <div class="summary-card"><span>主线分组</span><strong>${browserLines.length}</strong></div>
                <div class="summary-card"><span>当前主线</span><strong>${esc(state.dashboardLineFilter === 'all' ? '全部主线' : state.dashboardLineFilter)}</strong></div>
                <div class="summary-card"><span>推荐动作</span><strong>${esc(browserLines.length ? '先挑代表入口' : '先放宽筛选')}</strong></div>
              </div>
            </div>
            <div class="history-grid" style="margin-top:10px">
              <button class="history-card ${state.dashboardLineFilter === 'all' ? 'active' : ''}" type="button" onclick="setDashboardLineFilter('all')">
                <span>全部主线</span>
                <strong>${rows.length} 个结果</strong>
                <p>清空主线筛选，回到完整结果库。</p>
              </button>
              ${representativeCards || '<div class="detail-box"><strong>当前状态</strong><div>当前筛选范围内还没有足够结果形成代表入口，建议先放宽筛选或运行一轮标准实验。</div></div>'}
            </div>
            ${representativeDetail ? `
              <div class="history-detail" style="margin-top:12px; margin-bottom:0">
                <strong>代表结果与最近运行</strong>
                <p>代表结果用于稳定展示和汇报；最近运行用于回看刚跑完的实验。两者相同最好，但不强制相同。</p>
                <div class="detail-grid" style="margin-top:10px">
                  <div class="detail-box"><strong>当前代表结果</strong><div>${esc(representativeDetail.representative ? `${representativeDetail.representative.name} · ${representativeDashboardSummary(representativeDetail.representative)}` : '当前主线还没有稳定代表结果。')}</div></div>
                  <div class="detail-box"><strong>最近运行</strong><div>${esc(representativeDetail.latest ? `${representativeDetail.latest.name} · ${representativeDetail.latest.updated_at || '刚更新'}` : '当前主线还没有最近运行结果。')}</div></div>
                  <div class="detail-box"><strong>当前关系</strong><div>${esc(representativeDetail.latest && representativeDetail.latestIsRepresentative ? '最近运行已经足够稳定，可以直接继续作为代表结果。' : representativeDetail.latest ? '最近运行与代表结果分开保留，适合先回看，再决定是否替换展示入口。' : '先补出稳定结果，再逐步形成代表入口。')}</div></div>
                </div>
              </div>
            ` : ''}
            ${detailSemanticMarkup || (detailBinding ? standardAssetBindingHtml(detailBinding, {title: '当前代表资产', showToolbar: true}) : '')}
          `;
        }
      }
      renderResultsOverviewCockpit();
      if (!rows.length) {
        const emptyText = allRows.length
          ? '当前筛选条件下没有匹配的结果。可以清空筛选或切换状态。'
          : '还没有结果界面。运行一次实验后会自动生成。';
        document.getElementById('dashboards').innerHTML = `<div class="empty">${emptyText}</div>`;
        return;
      }
      document.getElementById('dashboards').innerHTML = `<div class="history-detail" style="margin-bottom:10px"><strong>完整结果表</strong><p>浏览驾驶舱只负责帮你挑入口；需要做完整筛选、逐条复查或打开任意结果时，再回到这张全量结果表。</p></div><table><thead><tr><th>名称</th><th>场景</th><th>主线</th><th>Run</th><th>通过率</th><th>操作</th></tr></thead><tbody>${rows.map(item => {
        const rate = `${Math.round((item.acceptance_rate || 0) * 1000) / 10}%`;
        return `<tr><td title="${esc(item.path)}">${esc(item.name)}</td><td>${esc(item.scenario || '—')}</td><td>${esc(resultLineForDashboard(item))}</td><td>${esc(item.run_count)}</td><td>${esc(rate)}</td><td><button onclick="showDashboard('${esc(item.path)}')">预览</button> <button class="secondary" onclick="window.open('${esc(item.url)}', '_blank')">打开</button></td></tr>`;
      }).join('')}</tbody></table>`;
    }

    function renderScenarioSummary(result, validated = false) {
      state.currentScenario = result.path;
      const markup = `
        <div class="summary-grid">
          <div class="summary-card"><span>场景名称</span><strong>${esc(result.name)}</strong></div>
          <div class="summary-card"><span>${validated ? '校验状态' : '环境'}</span><strong>${validated ? '已通过' : esc(result.environment || '—')}</strong></div>
          <div class="summary-card"><span>仿真时长</span><strong>${fmt(result.duration_s)} s</strong></div>
          <div class="summary-card"><span>积分步长</span><strong>${fmt(result.dt_s)} s</strong></div>
        </div>
        <div class="detail-grid">
          <div class="detail-box"><strong>系统构造</strong><div>${esc(result.system || '—')}</div></div>
          <div class="detail-box"><strong>控制器</strong><div>${esc(result.controller || '—')}</div></div>
          <div class="detail-box"><strong>随机种子</strong><div>${fmt(result.seed)}</div></div>
          <div class="detail-box"><strong>故障数量</strong><div>${fmt(result.fault_count)}</div></div>
        </div>
        <div class="detail-box"><strong>输出根目录</strong><div class="subtle">${esc(result.output_root || '—')}</div></div>
      `;
      document.getElementById('scenario-summary').innerHTML = markup;
      if (scenarioSummaryWorkspace) {
        scenarioSummaryWorkspace.innerHTML = markup;
      }
    }

    function acceptanceReasonLabel(reason) {
      const mapping = {
        max_final_error_deg: '末端误差超限',
        max_rms_error_deg: 'RMS 误差超限',
        max_peak_torque_nm: '峰值力矩超限',
      };
      return mapping[reason] || reason || '未说明';
    }

    function failureReasonsForRow(row) {
      const raw = String(row?.failed_acceptance || '').trim();
      if (!raw) return [];
      return raw.split(';').map(item => item.trim()).filter(Boolean);
    }

    function aggregateFailureReasons(rows) {
      const counts = new Map();
      for (const row of rows || []) {
        for (const reason of failureReasonsForRow(row)) {
          counts.set(reason, (counts.get(reason) || 0) + 1);
        }
      }
      return [...counts.entries()].sort((left, right) => right[1] - left[1]);
    }

    function peakHistoryValue(rows, key) {
      const values = (rows || [])
        .map(row => Number(row?.history_summary?.[key]))
        .filter(value => Number.isFinite(value));
      return values.length ? Math.max(...values) : null;
    }

    function disturbanceTermLabel(name) {
      const raw = String(name || '')
        .replace(/^peak_/, '')
        .replace(/_torque_norm_nm$/, '')
        .replace(/_torque_nm$/, '');
      const mapping = {
        gravity_gradient: '重力梯度',
        residual_magnetic: '残余磁矩',
        aerodynamic: '气动',
        solar_pressure: '太阳压',
      };
      return mapping[raw] || raw;
    }

    function parameterEntriesForRun(row) {
      return Object.entries(row || {}).filter(([key, value]) =>
        key.startsWith('param_') && value !== null && value !== undefined && value !== ''
      );
    }

    function parameterDiffSummary(best, worst) {
      const bestParams = new Map(parameterEntriesForRun(best));
      const diffs = parameterEntriesForRun(worst)
        .filter(([key, value]) => String(bestParams.get(key) ?? '') !== String(value ?? ''))
        .slice(0, 3);
      if (!diffs.length) return '';
      return diffs.map(([key, value]) => `${parameterColumnLabel(key)}=${value}`).join('，');
    }

    function dominantDisturbanceSummary(rows) {
      const candidates = (rows || [])
        .map(row => {
          const summary = row?.history_summary || {};
          return {
            run_id: row?.run_id,
            term: summary.dominant_disturbance_term,
            peak: Number(summary.dominant_disturbance_peak_nm),
          };
        })
        .filter(item => item.term && Number.isFinite(item.peak));
      if (!candidates.length) return '当前结果页还没有可用的环境扰动分解摘要。';
      candidates.sort((left, right) => right.peak - left.peak);
      const lead = candidates[0];
      return `${disturbanceTermLabel(lead.term)} 当前最突出，峰值约 ${fmt(lead.peak)} N m，来自 ${esc(lead.run_id || '当前 run')}。`;
    }

    function worstRunExplanation(result) {
      const best = result?.best_run || {};
      const worst = result?.worst_run || {};
      if (!worst.run_id) return '当前没有可解释的最差 run。';
      const reasons = failureReasonsForRow(worst);
      const diffText = parameterDiffSummary(best, worst);
      const theme = resultThemeLabel(result);
      const parts = [`最差 run 为 ${worst.run_id}，末端误差 ${fmt(worst.final_error_deg)} deg。`];
      if (reasons.length) {
        parts.push(`主要验收问题是 ${reasons.map(acceptanceReasonLabel).join('、')}。`);
      }
      if (diffText) {
        parts.push(`相对最佳 run，它的关键参数差异是 ${diffText}。`);
      } else if (worst.run_id !== best.run_id) {
        parts.push('它与最佳 run 没有明显的显式参数差异，更可能是随机样本、环境扰动或任务边界触发了退化。');
      }
      if (theme === '环境敏感性' && worst['param_system.environment']) {
        parts.push(`当前最差环境配置为 ${worst['param_system.environment']}，建议重点联动扰动分解和姿态误差曲线一起看。`);
      } else if (theme === '测量敏感性' && worst['param_sensors.gyro.noise_std_rad_s']) {
        parts.push(`当前最差噪声档位为 ${worst['param_sensors.gyro.noise_std_rad_s']} rad/s。`);
      } else if (theme === '鲁棒性' && (worst['param_time.seed'] || worst['param_monte_carlo.sample'])) {
        parts.push('当前更像是随机边界工况，适合继续回看该 run 的误差、扰动和执行力矩峰值。');
      }
      return parts.join('');
    }

    function modeTimelineSummary(result) {
      const timelineRows = Array.isArray(result?.timeline?.timeline) ? result.timeline.timeline : [];
      if (!timelineRows.length) return '当前结果没有 mission timeline。';
      const labels = timelineRows.map(item => item.mode || item.name || 'mode');
      return `共 ${timelineRows.length} 段，模式顺序：${labels.join(' -> ')}。`;
    }

    function diagnosticSummaryHtml(result) {
      const rows = Array.isArray(result?.runs) ? result.runs : [];
      const failureCounts = aggregateFailureReasons(rows);
      const peakOmega = peakHistoryValue(rows, 'peak_omega_rad_s');
      const peakAppliedTorque = peakHistoryValue(rows, 'peak_applied_torque_nm');
      const peakDisturbance = peakHistoryValue(rows, 'peak_disturbance_torque_nm');
      const failureHtml = failureCounts.length
        ? failureCounts.map(([reason, count]) => `<span class="chip">${esc(acceptanceReasonLabel(reason))} × ${count}</span>`).join('')
        : '<span class="chip">当前没有失败 run</span>';
      return `
        <div class="detail-grid" style="margin-top:10px">
          <div class="detail-box"><strong>验收失败原因</strong><div class="chips">${failureHtml}</div></div>
          <div class="detail-box"><strong>动态峰值摘要</strong><div>角速度峰值 ${fmt(peakOmega)} rad/s，执行力矩峰值 ${fmt(peakAppliedTorque)} N m，扰动力矩峰值 ${fmt(peakDisturbance)} N m。</div></div>
        </div>
        <div class="detail-grid" style="margin-top:10px">
          <div class="detail-box"><strong>主导扰动项</strong><div>${dominantDisturbanceSummary(rows)}</div></div>
          <div class="detail-box"><strong>最差 Run 解释</strong><div>${worstRunExplanation(result)}</div></div>
          <div class="detail-box"><strong>任务时间线</strong><div>${esc(modeTimelineSummary(result))}</div></div>
          <div class="detail-box"><strong>运行时摘要</strong><div>${result.runtime?.event_count ? `共 ${esc(result.runtime.event_count)} 个调度事件，当前可回放 ${esc((result.runtime.snapshots || []).length)} 个快照。` : '当前结果没有 runtime schedule。'}</div></div>
        </div>
      `;
    }

    function renderDashboardSummary(result) {
      const previousDashboard = state.currentDashboard;
      state.currentDashboard = result.path;
      state.currentDashboardUrl = result.url;
      state.currentDashboardData = result;
      if (previousDashboard !== result.path) {
        state.resultSummaryView = 'overview';
        state.resultWorkbenchView = 'focus';
        state.resultDetailView = 'summary';
        state.compareWorkbenchView = 'focus';
        state.compareSelection = {A: null, B: null};
        state.replayRun = null;
        state.runDetailRunId = null;
      }
      if (state.workspace?.dashboards) renderDashboards();
      const best = result.best_run || {};
      const worst = result.worst_run || {};
      const isLatest = state.latestRunDashboard && state.latestRunDashboard === result.path;
      const summaryView = state.resultSummaryView || 'overview';
      if (isLatest) {
        state.latestRunSummary = {...result};
        renderRunStatusPanels();
      }
      const matchedPlan = [...(state.workspace?.experiments || [])].find(plan => matchingDashboardForPlan(plan)?.path === result.path);
      const failurePairs = aggregateFailureReasons(result.runs || []);
      const failureSummary = failurePairs.length
        ? failurePairs.map(([reason, count]) => `${acceptanceReasonLabel(reason)} × ${count}`).join('；')
        : '当前没有失败 run';
      const parameterSummary = (result.parameter_columns || []).map(parameterColumnLabel).join(' / ') || '当前无显式参数列';
      const hasCompare = (result.compare_run_ids || []).length > 1;
      const hasReplay = Boolean(result.compare_histories && Object.keys(result.compare_histories).length);
      const nextActionSummary = Number(result.failed_count || 0) > 0
        ? '先诊断失败来源，再比较关键 run。'
        : '先读图确认代表结论，再沉淀为主线结果。';
      const overviewNodes = [
        {
          tone: 'accent',
          step: '研究问题',
          title: result.description || '当前实验重点是比较不同配置对闭环稳定性和控制性能的影响。',
          body: `${resultThemeLabel(result)} · run ${fmt(result.run_count)} · 变量 ${parameterSummary}`,
        },
        {
          tone: Number(result.acceptance_rate || 0) >= 0.999 ? 'ok' : 'warm',
          step: '当前观察',
          title: resultBestObservationText(result),
          body: resultJudgementText(result),
        },
        {
          tone: 'accent',
          step: '工程判断',
          title: `最佳 run ${result.best_run_id || '—'} / 最差 run ${result.worst_run_id || '—'}`,
          body: `通过率 ${Math.round((result.acceptance_rate || 0) * 1000) / 10}% · 最佳末端误差 ${fmt(result.best_final_error_deg)} deg · 最差末端误差 ${fmt(result.worst_final_error_deg)} deg`,
        },
        {
          tone: 'ok',
          step: '下一步',
          title: '继续推进实验主线',
          body: resultNextStepText(result),
        },
      ];
      const diagnosticsNodes = [
        {
          tone: Number(result.failed_count || 0) > 0 ? 'warm' : 'ok',
          step: '失败原因',
          title: failureSummary,
          body: '先判断失败是来自末端误差、RMS 误差还是峰值力矩超限。',
        },
        {
          tone: 'accent',
          step: '动态峰值',
          title: `角速度 ${fmt(peakHistoryValue(result.runs || [], 'peak_omega_rad_s'))} rad/s / 力矩 ${fmt(peakHistoryValue(result.runs || [], 'peak_applied_torque_nm'))} N m`,
          body: '再看峰值量是不是已经靠近任务或执行器边界。',
        },
        {
          tone: 'warm',
          step: '主导扰动',
          title: '环境与执行链诊断',
          body: dominantDisturbanceSummary(result.runs || []),
        },
        {
          tone: 'accent',
          step: '最差 Run',
          title: result.worst_run_id || '当前没有最差 run',
          body: worstRunExplanation(result),
        },
      ];
      const figureCards = resultFigureCards(result);
      const figurePackages = resultFigurePackages(result);
      const publicationCards = resultPublicationCards(result);
      const figureRibbon = figurePackages.length
        ? figurePackages.map(pkg => `<span class="ribbon-step">${esc(`${pkg.label} ${pkg.title}`)}</span>`).join('')
        : '<span class="ribbon-step">当前没有推荐图表入口</span>';
      const artifactChips = [
        `<span class="chip">参数列 ${(result.parameter_columns || []).length}</span>`,
        `<span class="chip">指标列 ${(result.metric_columns || []).length}</span>`,
        `<span class="chip">结果文件 ${(result.files || []).length}</span>`,
        `<span class="chip">最佳 run ${esc(result.best_run_id || '—')}</span>`,
      ].join('');
      const readingNodes = resultReadingWorkbenchNodes(result);
      const artifactGroups = resultArtifactGroups(result);
      const workbenchView = ['focus', 'route', 'actions'].includes(state.resultWorkbenchView) ? state.resultWorkbenchView : 'focus';
      state.resultWorkbenchView = workbenchView;
      const detailView = ['summary', 'runs', 'detail'].includes(state.resultDetailView) ? state.resultDetailView : 'summary';
      state.resultDetailView = detailView;
      const matchedPlanText = matchedPlan
        ? `${matchedPlan.name} · ${matchedPlan.path}`
        : '当前结果还没有直接匹配到稳定实验计划。';
      const workbenchMarkup = workbenchView === 'route'
        ? `
          <div class="history-detail" style="margin-bottom:0">
            <strong>结果阅读路线</strong>
            <p>先按“判定 -> 读图/对比 -> 链路/归档”的顺序走一遍，再进入下方细标签。这样当前结果会更像学术工作台，而不是一组并列功能。</p>
            <div class="flow-sequence" style="margin-top:10px">
              ${readingNodes.map(node => `
                <div class="flow-node ${esc(node.tone)}">
                  <small>${esc(node.step)}</small>
                  <strong>${esc(node.title)}</strong>
                  <p>${esc(node.body)}</p>
                  <div class="toolbar" style="margin-top:10px">
                    <button type="button" onclick="setResultSummaryView('${esc(node.action)}')">${esc(node.label)}</button>
                  </div>
                </div>
              `).join('')}
            </div>
            <div class="detail-grid" style="margin-top:12px">
              <div class="detail-box"><strong>优先图包</strong><div>${esc(figurePackages.slice(0, 2).map(pkg => `${pkg.label} ${pkg.title}`).join(' / ') || '当前没有稳定图包入口。')}</div></div>
              <div class="detail-box"><strong>对比入口</strong><div>${esc(hasCompare ? '当前已具备关键 Run 对比条件，适合继续看参数差异和曲线。' : '当前对比维度较少，建议先读概要和诊断。')}</div></div>
              <div class="detail-box"><strong>回放入口</strong><div>${esc(hasReplay ? '当前已具备姿态回放数据，适合复查动态过程和 mission timeline。' : '当前回放数据有限，更适合先读摘要和归档页。')}</div></div>
              <div class="detail-box"><strong>链路归档</strong><div>${esc(resultNextStepText(result))}</div></div>
            </div>
          </div>
        `
        : workbenchView === 'actions'
          ? `
            <div class="summary-grid">
              <div class="summary-card"><span>对应计划</span><strong title="${esc(matchedPlanText)}">${esc(matchedPlan ? matchedPlan.name : '待补计划')}</strong></div>
              <div class="summary-card"><span>对比能力</span><strong>${esc(hasCompare ? '已就绪' : '较弱')}</strong></div>
              <div class="summary-card"><span>回放能力</span><strong>${esc(hasReplay ? '已就绪' : '较弱')}</strong></div>
              <div class="summary-card"><span>结果出口</span><strong>${esc((result.files || []).some(file => file.name === 'dashboard.html') ? 'Dashboard 已就绪' : '静态页待补齐')}</strong></div>
            </div>
            <div class="detail-grid" style="margin-top:12px">
              <div class="detail-box"><strong>当前最短路径</strong><div>${esc(Number(result.failed_count || 0) > 0 ? '先看诊断，再进关键对比，最后回到计划收紧变量或验收。' : '先看图表和回放确认代表结论，再把结果沉淀为标准资产。')}</div></div>
              <div class="detail-box"><strong>对应计划入口</strong><div>${esc(matchedPlanText)}</div></div>
              <div class="detail-box"><strong>归档建议</strong><div>${esc((result.files || []).some(file => file.name === 'README.md') ? '当前结果已经具备 README，可继续补齐代表图和论文图出口。' : '建议先补齐 README、索引和摘要产物，再作为标准结果目录保留。')}</div></div>
              <div class="detail-box"><strong>下一条实验</strong><div>${esc(resultNextStepText(result))}</div></div>
            </div>
            <div class="toolbar" style="margin-top:12px">
              <button type="button" onclick="${matchedPlan ? `showExperiment('${esc(matchedPlan.path)}')` : 'void(0)'}" ${matchedPlan ? '' : 'disabled'}>查看对应计划</button>
              <button class="secondary" type="button" onclick="scrollToCompareView()" ${hasCompare ? '' : 'disabled'}>去结果对比</button>
              <button class="secondary" type="button" onclick="scrollToReplayView()" ${hasReplay ? '' : 'disabled'}>去姿态回放</button>
              <button class="secondary" type="button" onclick="scrollToPreviewView()">去 Dashboard 预览</button>
            </div>
          `
          : `
            <div class="summary-grid">
              <div class="summary-card"><span>实验主题</span><strong>${esc(resultThemeLabel(result))}</strong></div>
              <div class="summary-card"><span>当前判定</span><strong>${esc(Number(result.acceptance_rate || 0) >= 0.999 ? '整体通过' : '存在风险')}</strong></div>
              <div class="summary-card"><span>最佳 / 最差</span><strong>${esc(`${result.best_run_id || '—'} / ${result.worst_run_id || '—'}`)}</strong></div>
              <div class="summary-card"><span>下一动作</span><strong>${esc(Number(result.failed_count || 0) > 0 ? '先诊断' : '先读图')}</strong></div>
            </div>
            <div class="callout info" style="margin-top:12px; margin-bottom:0">
              <strong>当前结论</strong>
              <p>${esc(resultBestObservationText(result))} ${esc(resultJudgementText(result))}</p>
            </div>
            <div class="detail-grid" style="margin-top:12px">
              <div class="detail-box"><strong>研究问题</strong><div>${esc(result.description || '当前实验重点是比较不同配置对闭环稳定性和控制性能的影响。')}</div></div>
              <div class="detail-box"><strong>主要风险</strong><div>${esc(Number(result.failed_count || 0) > 0 ? `${failureSummary}。` : '当前没有失败 run，适合直接沉淀代表结论并继续扩主线。')}</div></div>
              <div class="detail-box"><strong>工程判断</strong><div>${esc(`通过率 ${Math.round((result.acceptance_rate || 0) * 1000) / 10}% · 最佳末端误差 ${fmt(result.best_final_error_deg)} deg · 最差末端误差 ${fmt(result.worst_final_error_deg)} deg`)}</div></div>
              <div class="detail-box"><strong>下一步建议</strong><div>${esc(nextActionSummary)} ${esc(resultNextStepText(result))}</div></div>
            </div>
          `;
      document.getElementById('result-summary').innerHTML = `
        ${isLatest ? `<div class="result-banner"><span>最新运行</span><strong>${esc(result.experiment_name || result.name)} 已完成，当前结果已自动同步到平台控制台。</strong></div>` : ''}
        <div class="summary-grid">
          <div class="summary-card"><span>实验名称</span><strong>${esc(result.experiment_name || result.name)}</strong></div>
          <div class="summary-card"><span>所属场景</span><strong>${esc(result.scenario_name || '—')}</strong></div>
          <div class="summary-card"><span>Run 数量</span><strong>${fmt(result.run_count)}</strong></div>
          <div class="summary-card"><span>通过率</span><strong>${Math.round((result.acceptance_rate || 0) * 1000) / 10}%</strong></div>
          <div class="summary-card"><span>最佳 Run</span><strong>${esc(result.best_run_id || '—')}</strong></div>
          <div class="summary-card"><span>最佳末端误差</span><strong>${fmt(result.best_final_error_deg)} deg</strong></div>
        </div>
        <div class="editor-view-shell" style="margin-top:10px">
          <div class="segment-control">
            <button class="${workbenchView === 'focus' ? 'active' : ''}" type="button" onclick="setResultWorkbenchView('focus')">判定焦点</button>
            <button class="${workbenchView === 'route' ? 'active' : ''}" type="button" onclick="setResultWorkbenchView('route')">读图路线</button>
            <button class="${workbenchView === 'actions' ? 'active' : ''}" type="button" onclick="setResultWorkbenchView('actions')">推进动作</button>
          </div>
          <div class="editor-view-note">${esc(resultWorkbenchViewNote(workbenchView))}</div>
        </div>
        <div class="history-detail" style="margin:10px 0">
          <strong>结果阅读驾驶舱</strong>
          <p>首屏先固定成“判定焦点 / 读图路线 / 推进动作”三块工作区，再进入下方的概要、诊断、图表、链路和产物标签。</p>
          ${workbenchMarkup}
        </div>
        <div class="result-summary-shell">
          <div class="result-summary-head">
            <div class="segment-control">
              <button class="${summaryView === 'overview' ? 'active' : ''}" type="button" onclick="setResultSummaryView('overview')">概要</button>
              <button class="${summaryView === 'diagnostics' ? 'active' : ''}" type="button" onclick="setResultSummaryView('diagnostics')">诊断</button>
              <button class="${summaryView === 'figures' ? 'active' : ''}" type="button" onclick="setResultSummaryView('figures')">图表</button>
              <button class="${summaryView === 'roadmap' ? 'active' : ''}" type="button" onclick="setResultSummaryView('roadmap')">链路</button>
              <button class="${summaryView === 'artifacts' ? 'active' : ''}" type="button" onclick="setResultSummaryView('artifacts')">产物</button>
            </div>
            <div class="result-summary-note">结果摘要标签：${esc(resultSummaryViewNote(summaryView))}</div>
          </div>
          <div class="result-summary-panel" ${summaryView === 'overview' ? '' : 'hidden'}>
            ${resultAcademicBriefHtml(result, matchedPlan)}
            ${resultTakeawayCardsHtml(result, matchedPlan)}
            <div class="history-detail" style="margin:10px 0 0">
              <strong>结果摘要画布</strong>
              <p>用固定四步压住研究问题、观察、工程判断和下一步。</p>
              <div class="flow-sequence" style="margin-top:10px">
                ${overviewNodes.map(node => `
                  <div class="flow-node ${esc(node.tone)}">
                    <small>${esc(node.step)}</small>
                    <strong>${esc(node.title)}</strong>
                    <p>${esc(node.body)}</p>
                  </div>
                `).join('')}
              </div>
              <div class="detail-grid" style="margin-top:10px">
                <div class="detail-box"><strong>最佳 Run 指标</strong><div>末端误差 ${fmt(best.final_error_deg)} deg / RMS ${fmt(best.rms_error_deg)} deg</div></div>
                <div class="detail-box"><strong>最差 Run 指标</strong><div>${esc(result.worst_run_id || '—')} / 末端误差 ${fmt(result.worst_final_error_deg)} deg</div></div>
                <div class="detail-box"><strong>变量维度</strong><div>${esc(parameterSummary)}</div></div>
                <div class="detail-box"><strong>计划绑定</strong><div>${esc(matchedPlan ? matchedPlan.name : '当前结果还未绑定稳定计划。')}</div></div>
              </div>
            </div>
            <details class="fold-panel" style="margin-top:10px">
              <summary>展开实验结论导读<span>按需查看完整 7 项说明和结果浏览建议</span></summary>
              <div class="fold-panel-body">
                ${resultResearchHtml(result)}
                ${resultGuideHtml(result)}
              </div>
            </details>
            <div class="toolbar" style="margin:10px 0 0">
              <button type="button" onclick="setResultSummaryView('figures')">看学术图包</button>
              <button type="button" onclick="${matchedPlan ? `showExperiment('${esc(matchedPlan.path)}')` : 'void(0)'}" ${matchedPlan ? '' : 'disabled'}>查看对应计划</button>
              <button class="secondary" type="button" onclick="${matchedPlan ? `runPlan('${esc(matchedPlan.path)}')` : 'void(0)'}" ${matchedPlan ? '' : 'disabled'}>重新运行这个计划</button>
            </div>
          </div>
          <div class="result-summary-panel" ${summaryView === 'diagnostics' ? '' : 'hidden'}>
            <div class="history-detail" style="margin-bottom:10px">
              <strong>诊断工作台</strong>
              <p>先看失败、峰值、主导扰动和最差 run，再决定回计划还是继续读图。</p>
              <div class="summary-grid" style="margin-top:10px">
                <div class="summary-card"><span>失败 Run</span><strong>${fmt(result.failed_count || 0)}</strong></div>
                <div class="summary-card"><span>最差 Run</span><strong>${esc(result.worst_run_id || '—')}</strong></div>
                <div class="summary-card"><span>峰值力矩</span><strong>${fmt(peakHistoryValue(result.runs || [], 'peak_applied_torque_nm'))} N m</strong></div>
                <div class="summary-card"><span>推荐动作</span><strong>${esc(Number(result.failed_count || 0) > 0 ? '回计划收紧' : '继续对比 / 回放')}</strong></div>
              </div>
              <div class="flow-sequence" style="margin-top:10px">
                ${diagnosticsNodes.map(node => `
                  <div class="flow-node ${esc(node.tone)}">
                    <small>${esc(node.step)}</small>
                    <strong>${esc(node.title)}</strong>
                    <p>${esc(node.body)}</p>
                  </div>
                `).join('')}
              </div>
            </div>
            ${diagnosticSummaryHtml(result)}
            <div class="mini-table">
              <table>
                <thead><tr><th>Run</th><th>Accepted</th><th>Final error deg</th><th>RMS error deg</th></tr></thead>
                <tbody>
                  ${result.runs.slice(0, 4).map(row => `<tr><td>${esc(row.run_id)}</td><td>${esc(row.accepted)}</td><td>${fmt(row.final_error_deg)}</td><td>${fmt(row.rms_error_deg)}</td></tr>`).join('')}
                </tbody>
              </table>
            </div>
            <div class="toolbar" style="margin-top:10px">
              <button type="button" onclick="scrollToCompareView()" ${hasCompare ? '' : 'disabled'}>去结果对比</button>
              <button class="secondary" type="button" onclick="scrollToReplayView()" ${hasReplay ? '' : 'disabled'}>去姿态回放</button>
              <button class="secondary" type="button" onclick="${matchedPlan ? `showExperiment('${esc(matchedPlan.path)}')` : 'void(0)'}" ${matchedPlan ? '' : 'disabled'}>回到对应计划</button>
            </div>
          </div>
          <div class="result-summary-panel" ${summaryView === 'figures' ? '' : 'hidden'}>
            ${resultFigureWorkbenchHtml(result)}
            <div class="history-detail" style="margin-bottom:10px">
              <strong>代表图与论文图出口</strong>
              <p>这里先明确这次实验最适合作为代表图、论文图和演示图的出口，再进入下面的学术图包导览，减少“图很多但不知道先用哪几张”的情况。</p>
              <div class="figure-guide-grid" style="margin-top:10px">
                ${publicationCards.map(card => `
                  <div class="figure-guide-card">
                    <span>${esc(card.label)}</span>
                    <strong>${esc(card.title)}</strong>
                    <p>${esc(card.summary)}</p>
                    <div class="chips" style="margin-top:8px">${(card.contents || []).map(item => `<span class="chip">${esc(item)}</span>`).join('')}</div>
                    <div class="toolbar" style="margin-top:10px">
                      <button type="button" onclick="${card.actionCode || 'void(0)'}" ${card.actionCode ? '' : 'disabled'}>${esc(card.actionLabel || '查看')}</button>
                    </div>
                  </div>
                `).join('')}
              </div>
            </div>
            <details class="fold-panel">
              <summary>图表阅读路径<span>按需展开图包顺序和关键图卡</span></summary>
              <div class="fold-panel-body">
                <div class="history-detail" style="margin:10px 0 0">
                  <strong>图表阅读路径</strong>
                  <p>先按固定学术图包阅读，再进入关键图卡、对比和回放，让不同实验共享一致的分析节奏，同时保留各自主重点。</p>
                  <div class="ribbon">
                    ${figureRibbon}
                  </div>
                </div>
                ${resultFigureGuideHtml(result)}
              </div>
            </details>
          </div>
          <div class="result-summary-panel" ${summaryView === 'roadmap' ? '' : 'hidden'}>
            <div class="history-detail" style="margin-bottom:10px">
              <strong>实验链路工作台</strong>
              <p>把当前结果放回标准实验主线里，判断它属于哪一环、下一组实验该接到哪里，以及是否需要回到计划做派生。</p>
            </div>
            ${resultRoadmapHtml(result)}
            <details class="fold-panel">
              <summary>链路细读面板<span>按需展开推荐动作和后续实验导读</span></summary>
              <div class="fold-panel-body">
                ${resultGuideHtml(result)}
              </div>
            </details>
          </div>
          <div class="result-summary-panel" ${summaryView === 'artifacts' ? '' : 'hidden'}>
            <div class="history-detail" style="margin-bottom:10px">
              <strong>结果产物面板</strong>
              <p>这里集中看参数列、指标列和文件产物，方便把这次实验作为标准结果目录归档、分享或继续做二次分析。</p>
              <div class="chips" style="margin-top:10px">${artifactChips}</div>
            </div>
            <div class="detail-box"><strong>参数列</strong><div class="chips">${(result.parameter_columns || []).map(name => `<span class="chip">${esc(name)}</span>`).join('') || '<span class="subtle">暂无</span>'}</div></div>
            <div class="detail-box" style="margin-top:10px"><strong>指标列</strong><div class="chips">${(result.metric_columns || []).map(name => `<span class="chip">${esc(name)}</span>`).join('') || '<span class="subtle">暂无</span>'}</div></div>
            <div class="figure-guide-grid" style="margin-top:10px">
              ${artifactGroups.map(group => `
                <div class="figure-guide-card">
                  <span>${esc(group.title)}</span>
                  <strong>${esc(group.files.length)} 个文件</strong>
                  <p>${esc(group.description)}</p>
                  <div class="files">${group.files.length ? group.files.map(file => `<a href="${file.url}" target="_blank">${esc(file.name)}</a>`).join('') : '<span class="subtle">当前没有这类文件。</span>'}</div>
                </div>
              `).join('')}
            </div>
          </div>
        </div>
        <div class="history-detail" style="margin-top:10px; margin-bottom:0">
          <strong>Run 工作台</strong>
          <p>把多 run 结果压成“运行概况 / Run 排行 / Run 明细”三块切换，避免结果页重新退回长列表。</p>
          <div class="editor-view-shell" style="margin-top:10px">
            <div class="segment-control">
              <button class="${detailView === 'summary' ? 'active' : ''}" type="button" onclick="setResultDetailView('summary')">运行概况</button>
              <button class="${detailView === 'runs' ? 'active' : ''}" type="button" onclick="setResultDetailView('runs')">Run 排行</button>
              <button class="${detailView === 'detail' ? 'active' : ''}" type="button" onclick="setResultDetailView('detail')">Run 明细</button>
            </div>
            <div class="editor-view-note">${esc(resultDetailViewNote(detailView))}</div>
          </div>
          <div id="result-run-summary-panel" ${detailView === 'summary' ? '' : 'hidden'} style="margin-top:10px"></div>
          <div id="run-workbench-panel" ${detailView === 'runs' ? '' : 'hidden'} style="margin-top:10px"></div>
          <div id="run-detail-panel" ${detailView === 'detail' ? '' : 'hidden'} style="margin-top:10px"></div>
        </div>
      `;
      previewTitle.textContent = `${result.experiment_name || result.name} / ${result.scenario_name || '未命名场景'}`;
      previewFrame.src = result.url;
      openDashboard.disabled = false;
      renderPreviewWorkbench(result);
      renderRunSummary(result);
      renderRunWorkbench(result);
      renderRunDetails(result);
      renderCompareView(result);
      renderReplayView(result);
    }

    function clearDashboardPreview() {
      state.currentDashboard = null;
      state.currentDashboardUrl = null;
      state.currentDashboardData = null;
      state.resultSummaryView = 'overview';
      state.resultWorkbenchView = 'focus';
      state.resultDetailView = 'summary';
      state.compareWorkbenchView = 'focus';
      state.compareSelection = {A: null, B: null};
      state.replayWorkbenchView = 'focus';
      state.replayRun = null;
      state.runDetailRunId = null;
      document.getElementById('result-summary').innerHTML = '运行实验或选择一个结果目录后，这里会显示实验摘要、最佳 run 和关键文件。';
      compareView.innerHTML = '选择结果目录后，这里会显示最佳/最差或关键 run 的对照指标和曲线。';
      replayView.innerHTML = '选择结果目录后，这里会显示基于真实四元数的姿态回放。';
      stopReplayAnimation();
      previewTitle.textContent = '还没有加载结果界面';
      state.previewViewMode = 'brief';
      if (previewWorkbench) {
        previewWorkbench.hidden = true;
        previewWorkbench.innerHTML = '';
      }
      previewFrame.hidden = true;
      previewFrame.removeAttribute('src');
      previewEmpty.hidden = false;
      openDashboard.disabled = true;
      renderResultsOverviewCockpit();
    }

    function renderRunDetails(result) {
      const host = document.getElementById('run-detail-panel');
      const rows = Array.isArray(result.runs) ? result.runs : [];
      if (!host) return;
      if (!rows.length) {
        host.innerHTML = '<div class="empty">当前结果没有可展开的 run 明细。</div>';
        return;
      }
      const defaultRunId = rows.some(row => row.run_id === state.runDetailRunId)
        ? state.runDetailRunId
        : (result.best_run_id || rows[0]?.run_id);
      host.innerHTML = `
        <div class="history-detail" style="margin-bottom:0">
          <strong>Run 明细</strong>
          <div class="compare-toolbar" style="margin-top:10px">
            <label>选择 Run<select id="run-detail-select">${rows.map(row => `<option value="${esc(row.run_id)}">${esc(row.run_id)}</option>`).join('')}</select></label>
            <div class="detail-box"><strong>说明</strong><div class="subtle">在这里查看单个 run 的验收结果、参数和输出文件，不必离开平台页面。</div></div>
          </div>
          <div id="run-detail-body"></div>
        </div>
      `;
      const select = document.getElementById('run-detail-select');
      const body = document.getElementById('run-detail-body');
      select.value = defaultRunId;
      const update = () => {
        const row = rows.find(item => item.run_id === select.value) || rows[0];
        state.runDetailRunId = row.run_id || null;
        const parameterEntries = Object.entries(row).filter(([key, value]) => key.startsWith('param_') && value !== null && value !== undefined && value !== '');
        const artifactEntries = Object.entries(row.artifacts || {});
        const failureReasons = failureReasonsForRow(row);
        const historySummary = row.history_summary || {};
        const canCompare = Array.isArray(result.compare_run_ids) && result.compare_run_ids.includes(row.run_id);
        const canReplay = Boolean(result.compare_histories?.[row.run_id]?.length);
        const activeUses = [];
        if (state.compareSelection.A === row.run_id) activeUses.push('当前用于对比 A');
        if (state.compareSelection.B === row.run_id) activeUses.push('当前用于对比 B');
        if (state.replayRun === row.run_id) activeUses.push('当前用于姿态回放');
        const bestParameterEntries = Object.entries(result.best_run || {}).filter(([key, value]) => key.startsWith('param_') && value !== null && value !== undefined && value !== '');
        const bestParameters = new Map(bestParameterEntries);
        const deltaEntries = parameterEntries.filter(([key, value]) => String(bestParameters.get(key) ?? '') !== String(value ?? ''));
        const bestOnlyEntries = [...bestParameters.entries()].filter(([key]) => !parameterEntries.some(([name]) => name === key));
        body.innerHTML = `
          <div class="summary-grid">
            <div class="summary-card"><span>Run</span><strong>${esc(row.run_id || '—')}</strong></div>
            <div class="summary-card"><span>验收状态</span><strong>${esc(row.accepted ?? '—')}</strong></div>
            <div class="summary-card"><span>末端误差</span><strong>${fmt(row.final_error_deg)} deg</strong></div>
            <div class="summary-card"><span>RMS 误差</span><strong>${fmt(row.rms_error_deg)} deg</strong></div>
            <div class="summary-card"><span>峰值力矩</span><strong>${fmt(row.peak_torque_nm)} N m</strong></div>
            <div class="summary-card"><span>输出目录</span><strong title="${esc(row.output_dir || '—')}">${esc(row.output_dir || '—')}</strong></div>
          </div>
          <div class="detail-box"><strong>当前角色</strong><div class="chips">${activeUses.length ? activeUses.map(text => `<span class="chip">${esc(text)}</span>`).join('') : '<span class="subtle">当前没有被送到对比区或回放区。</span>'}</div></div>
          <div class="detail-grid">
            <div class="detail-box"><strong>关键指标</strong><div>最终误差 ${fmt(row.final_error_deg)} deg，RMS ${fmt(row.rms_error_deg)} deg，峰值力矩 ${fmt(row.peak_torque_nm)} N m。</div></div>
            <div class="detail-box"><strong>参数摘要</strong><div>${parameterEntries.length ? parameterEntries.map(([key, value]) => `${esc(key)}=${esc(value)}`).join('，') : '该 run 没有额外参数扫描列。'}</div></div>
            <div class="detail-box"><strong>验收失败原因</strong><div class="chips">${failureReasons.length ? failureReasons.map(reason => `<span class="chip">${esc(acceptanceReasonLabel(reason))}</span>`).join('') : '<span class="chip">当前 run 已通过验收</span>'}</div></div>
            <div class="detail-box"><strong>动态摘要</strong><div>角速度峰值 ${fmt(historySummary.peak_omega_rad_s)} rad/s，执行力矩峰值 ${fmt(historySummary.peak_applied_torque_nm)} N m，扰动力矩峰值 ${fmt(historySummary.peak_disturbance_torque_nm)} N m。</div></div>
            <div class="detail-box"><strong>主导扰动项</strong><div>${historySummary.dominant_disturbance_term ? `${disturbanceTermLabel(historySummary.dominant_disturbance_term)}，峰值 ${fmt(historySummary.dominant_disturbance_peak_nm)} N m。` : '当前 run 没有扰动分解摘要。'}</div></div>
          </div>
          <div class="toolbar" style="margin-top:10px">
            <button type="button" onclick="${canCompare ? `selectCompareRun('A','${esc(row.run_id)}')` : 'void(0)'}" ${canCompare ? '' : 'disabled'}>送到对比 A</button>
            <button class="secondary" type="button" onclick="${canCompare ? `selectCompareRun('B','${esc(row.run_id)}')` : 'void(0)'}" ${canCompare ? '' : 'disabled'}>送到对比 B</button>
            <button class="secondary" type="button" onclick="${canReplay ? `selectReplayRun('${esc(row.run_id)}')` : 'void(0)'}" ${canReplay ? '' : 'disabled'}>设为回放 Run</button>
          </div>
          <div class="detail-box"><strong>参数列</strong><div class="chips">${parameterEntries.length ? parameterEntries.map(([key, value]) => `<span class="chip">${esc(key)}=${esc(value)}</span>`).join('') : '<span class="subtle">暂无</span>'}</div></div>
          <div class="detail-box" style="margin-top:10px"><strong>与最佳 Run 的参数差异</strong><div class="chips">${
            row.run_id === result.best_run_id
              ? '<span class="chip">当前就是最佳 Run</span>'
              : (deltaEntries.length || bestOnlyEntries.length)
                ? [
                    ...deltaEntries.map(([key, value]) => `<span class="chip">${esc(key)}=${esc(value)}，最佳为 ${esc(bestParameters.get(key))}</span>`),
                    ...bestOnlyEntries.map(([key, value]) => `<span class="chip">${esc(key)} 未在当前 run 中显式给出，最佳为 ${esc(value)}</span>`),
                  ].join('')
                : '<span class="subtle">当前 run 与最佳 run 在参数列上没有显式差异。</span>'
          }</div></div>
          <div class="files" style="margin-top:10px">${artifactEntries.length ? artifactEntries.map(([name, url]) => `<a href="${url}" target="_blank">${esc(name)}</a>`).join('') : '<span class="subtle">当前 run 没有可访问的产物文件。</span>'}</div>
        `;
        renderRunWorkbench(result);
      };
      select.addEventListener('change', update);
      update();
    }

    function renderRunSummary(result) {
      const host = document.getElementById('result-run-summary-panel');
      const rows = rankedRuns(result);
      if (!host) return;
      if (!rows.length) {
        host.innerHTML = '<div class="empty">当前结果没有可汇总的 run 概况。</div>';
        return;
      }
      const best = rows.find(row => row.run_id === result.best_run_id) || rows[0];
      const worst = rows.find(row => row.run_id === result.worst_run_id) || rows[rows.length - 1];
      const acceptedRows = rows.filter(row => String(row.accepted).toLowerCase() === 'true');
      const failedRows = rows.filter(row => String(row.accepted).toLowerCase() !== 'true');
      const compareReadyRows = rows.filter(row => (result.compare_run_ids || []).includes(row.run_id));
      const replayReadyRows = rows.filter(row => result.compare_histories?.[row.run_id]?.length);
      host.innerHTML = `
        <div class="summary-grid">
          <div class="summary-card"><span>通过 / 失败</span><strong>${acceptedRows.length} / ${failedRows.length}</strong></div>
          <div class="summary-card"><span>最佳 Run</span><strong>${esc(best.run_id || '—')}</strong></div>
          <div class="summary-card"><span>最差 Run</span><strong>${esc(worst.run_id || '—')}</strong></div>
          <div class="summary-card"><span>可对比 / 可回放</span><strong>${compareReadyRows.length} / ${replayReadyRows.length}</strong></div>
        </div>
        <div class="detail-grid" style="margin-top:10px">
          <div class="detail-box"><strong>最佳 Run 摘要</strong><div>${esc(best.run_id || '—')} · 末端误差 ${fmt(best.final_error_deg)} deg · RMS ${fmt(best.rms_error_deg)} deg · 峰值力矩 ${fmt(best.peak_torque_nm)} N m。</div></div>
          <div class="detail-box"><strong>最差 Run 摘要</strong><div>${esc(worst.run_id || '—')} · 末端误差 ${fmt(worst.final_error_deg)} deg · ${failureReasonsForRow(worst).length ? failureReasonsForRow(worst).map(acceptanceReasonLabel).join(' / ') : '当前没有明确失败原因。'}</div></div>
          <div class="detail-box"><strong>当前建议</strong><div>${esc(failedRows.length ? '先进入 Run 排行锁定失败 run，再查看单 run 明细或送去对比。' : '先看最佳与次优 run 的差异，再决定是否继续对比或直接沉淀代表结果。')}</div></div>
          <div class="detail-box"><strong>下一动作</strong><div>${esc(failedRows.length ? '去 Run 排行' : '看最佳 Run 明细')}，然后${esc(compareReadyRows.length ? '送去结果对比' : '回到结果图包')}。</div></div>
        </div>
        <div class="toolbar" style="margin-top:10px">
          <button type="button" onclick="setResultDetailView('runs')">查看 Run 排行</button>
          <button class="secondary" type="button" onclick="focusRunDetail('${esc(best.run_id || '')}')" ${best.run_id ? '' : 'disabled'}>查看最佳 Run 明细</button>
          <button class="secondary" type="button" onclick="focusRunDetail('${esc(worst.run_id || '')}')" ${worst.run_id ? '' : 'disabled'}>查看较差 Run 明细</button>
          <button class="secondary" type="button" onclick="${compareReadyRows.length ? `selectCompareRun('A','${esc(compareReadyRows[0].run_id)}')` : 'void(0)'}" ${compareReadyRows.length ? '' : 'disabled'}>送最佳候选到对比 A</button>
        </div>
      `;
    }

    function rankedRuns(result) {
      const rows = Array.isArray(result.runs) ? [...result.runs] : [];
      return rows.sort((left, right) => {
        const acceptedLeft = String(left.accepted).toLowerCase() === 'true' ? 0 : 1;
        const acceptedRight = String(right.accepted).toLowerCase() === 'true' ? 0 : 1;
        if (acceptedLeft !== acceptedRight) return acceptedLeft - acceptedRight;
        const finalLeft = Number.isFinite(Number(left.final_error_deg)) ? Number(left.final_error_deg) : Number.POSITIVE_INFINITY;
        const finalRight = Number.isFinite(Number(right.final_error_deg)) ? Number(right.final_error_deg) : Number.POSITIVE_INFINITY;
        if (finalLeft !== finalRight) return finalLeft - finalRight;
        return String(left.run_id || '').localeCompare(String(right.run_id || ''));
      });
    }

    function runRoleChips(row, result) {
      const chips = [];
      if (row.run_id === result.best_run_id) chips.push('<span class="chip">最佳</span>');
      if (row.run_id === result.worst_run_id) chips.push('<span class="chip">最差</span>');
      if (row.run_id === state.runDetailRunId) chips.push('<span class="chip">当前明细</span>');
      if (row.run_id === state.compareSelection.A) chips.push('<span class="chip">对比 A</span>');
      if (row.run_id === state.compareSelection.B) chips.push('<span class="chip">对比 B</span>');
      if (row.run_id === state.replayRun) chips.push('<span class="chip">回放</span>');
      return chips.join('') || '<span class="subtle">—</span>';
    }

    function focusRunDetail(runId) {
      state.runDetailRunId = runId || null;
      if (state.currentDashboardData) {
        state.resultDetailView = 'detail';
        renderDashboardSummary(state.currentDashboardData);
        syncRouteHash();
      }
      const select = document.getElementById('run-detail-select');
      if (!select) return;
      if (![...select.options].some(option => option.value === runId)) return;
      select.value = runId;
      select.dispatchEvent(new Event('change'));
      document.getElementById('run-detail-panel')?.scrollIntoView({behavior: 'smooth', block: 'start'});
    }

    function renderRunWorkbench(result) {
      const host = document.getElementById('run-workbench-panel');
      if (!host) return;
      const rows = rankedRuns(result);
      if (!rows.length) {
        host.innerHTML = '<div class="empty">当前结果没有可供浏览的 run 排行。</div>';
        return;
      }
      host.innerHTML = `
        <div class="history-detail" style="margin-bottom:0">
          <strong>Run 排行与工作台</strong>
          <p style="margin:8px 0 12px;color:var(--muted);font-size:12px;line-height:1.5">按验收状态和末端误差排序。你可以从这里直接查看某个 run 的明细，或把它送去对比和回放。</p>
          <div class="mini-table">
            <table>
              <thead><tr><th>排名</th><th>Run</th><th>状态</th><th>末端误差</th><th>角色</th><th>操作</th></tr></thead>
              <tbody>
                ${rows.map((row, index) => `
                  <tr>
                    <td>${index + 1}</td>
                    <td>${esc(row.run_id || '—')}</td>
                    <td>${esc(row.accepted ?? '—')}</td>
                    <td>${fmt(row.final_error_deg)}</td>
                    <td><div class="chips">${runRoleChips(row, result)}</div></td>
                    <td>
                      <button type="button" onclick="focusRunDetail('${esc(row.run_id)}')">查看</button>
                      <button class="secondary" type="button" onclick="${(result.compare_run_ids || []).includes(row.run_id) ? `selectCompareRun('A','${esc(row.run_id)}')` : 'void(0)'}" ${(result.compare_run_ids || []).includes(row.run_id) ? '' : 'disabled'}>A</button>
                      <button class="secondary" type="button" onclick="${(result.compare_run_ids || []).includes(row.run_id) ? `selectCompareRun('B','${esc(row.run_id)}')` : 'void(0)'}" ${(result.compare_run_ids || []).includes(row.run_id) ? '' : 'disabled'}>B</button>
                      <button class="secondary" type="button" onclick="${result.compare_histories?.[row.run_id]?.length ? `selectReplayRun('${esc(row.run_id)}')` : 'void(0)'}" ${result.compare_histories?.[row.run_id]?.length ? '' : 'disabled'}>回放</button>
                    </td>
                  </tr>
                `).join('')}
              </tbody>
            </table>
          </div>
        </div>
      `;
    }

    function parameterEntriesForRow(row) {
      return Object.entries(row || {}).filter(([key, value]) => key.startsWith('param_') && value !== null && value !== undefined && value !== '');
    }

    function parameterDifferenceEntries(baseRow, otherRow) {
      const base = new Map(parameterEntriesForRow(baseRow));
      const other = new Map(parameterEntriesForRow(otherRow));
      const keys = new Set([...base.keys(), ...other.keys()]);
      return [...keys].filter(key => String(base.get(key) ?? '') !== String(other.get(key) ?? '')).map(key => ({
        key,
        base: base.get(key),
        other: other.get(key),
      }));
    }

    function parameterDifferenceChips(baseRow, otherRow, labels = {base: 'A', other: 'B'}) {
      const entries = parameterDifferenceEntries(baseRow, otherRow);
      if (!entries.length) {
        return '<span class="subtle">当前两组 run 在参数列上没有显式差异。</span>';
      }
      return entries.map(item => `<span class="chip">${esc(parameterColumnLabel(item.key))}: ${labels.base}=${esc(item.base ?? '—')} / ${labels.other}=${esc(item.other ?? '—')}</span>`).join('');
    }

    function compareReadingMeta(result, runA, runB) {
      const theme = resultThemeLabel(result);
      const errorA = Number(runA?.final_error_deg);
      const errorB = Number(runB?.final_error_deg);
      const torqueA = Number(runA?.peak_torque_nm);
      const torqueB = Number(runB?.peak_torque_nm);
      const betterRun = Number.isFinite(errorA) && Number.isFinite(errorB) ? (errorA <= errorB ? 'A' : 'B') : '';
      const lowerTorqueRun = Number.isFinite(torqueA) && Number.isFinite(torqueB) ? (torqueA <= torqueB ? 'A' : 'B') : '';
      const summaryParts = [];
      if (betterRun) summaryParts.push(`当前末端误差更优的是 Run ${betterRun}。`);
      if (lowerTorqueRun && lowerTorqueRun !== betterRun) {
        summaryParts.push(`但峰值力矩更低的是 Run ${lowerTorqueRun}，说明两者存在性能与控制动作权衡。`);
      } else if (lowerTorqueRun) {
        summaryParts.push(`同时峰值力矩也主要由 Run ${lowerTorqueRun} 保持在更低或相近水平。`);
      }
      const themeHints = {
        '控制器整定': {
          attitude: '先看谁收敛更快、谁振荡更明显，再判断参数是在改善性能还是只是在放大控制动作。',
          torque: '再看力矩曲线是否明显抬高峰值；若误差改善有限但力矩显著升高，通常说明参数过于激进。',
        },
        '鲁棒性': {
          attitude: '重点找边界工况而不是只看最漂亮的曲线，优先观察最差 run 是否出现长时间偏差或突发尖峰。',
          torque: '如果误差退化时力矩没有同步抬高，问题更可能来自随机扰动或估计边界，而不是执行器饱和。',
        },
        '环境敏感性': {
          attitude: '比较环境切换后误差曲线整体是否抬升，这能直接说明环境建模是否已经影响闭环质量。',
          torque: '若 orbital 条件下力矩曲线整体上移，就值得继续做扰动分解和环境预算分析。',
        },
        '扰动分解': {
          attitude: '看不同扰动模板下误差是谁先恶化，帮助识别主导扰动项对姿态性能的影响。',
          torque: '若力矩曲线的变化与主导扰动项一致，说明后续高保真建模应优先落在该扰动上。',
        },
        '测量敏感性': {
          attitude: '优先看噪声增大后误差曲线是否更抖、更慢收敛，判断感知链退化从哪里先暴露。',
          torque: '若误差变差而力矩变化不大，通常意味着问题更偏测量与估计，而不是控制输出不足。',
        },
        '任务模式切换': {
          attitude: '重点关注切换段误差尖峰和持续时间，而不是只看最终误差。',
          torque: '若切换段力矩脉冲过大，后续应联动回放与 runtime 一起看任务设计是否过激。',
        },
        '执行器边界': {
          attitude: '看能力下降后误差是渐进恶化还是突然失稳，后者更像真实边界被触发。',
          torque: '若力矩曲线已经贴近上界或明显抬高，瓶颈更偏执行机构能力而非简单参数问题。',
        },
        '轮速管理': {
          attitude: '先确认更积极的动量管理是否干扰了姿态误差曲线的平稳性。',
          torque: '再看力矩曲线是否更平顺或更激进，结合回放判断长期执行器余量是否改善。',
        },
      };
      const hints = themeHints[theme] || {
        attitude: '先比较误差曲线谁更平稳、谁更快收敛，再结合参数差异判断主要影响来自哪里。',
        torque: '再比较力矩曲线谁更激进、谁更保守，帮助判断性能改善是否值得。',
      };
      return {
        summary: summaryParts.join(' ') || '当前两组 run 适合从误差和力矩两条曲线一起看，先判断谁更优，再判断代价来自哪里。',
        attitude: hints.attitude,
        torque: hints.torque,
      };
    }

    function preferredReplayRunIdFromCompare(runA, runB) {
      const errorA = Number(runA?.final_error_deg);
      const errorB = Number(runB?.final_error_deg);
      if (Number.isFinite(errorA) && Number.isFinite(errorB)) {
        return errorA <= errorB ? runA?.run_id : runB?.run_id;
      }
      return runA?.run_id || runB?.run_id || '';
    }

    function compareFailureSummary(runA, runB) {
      const reasonsA = failureReasonsForRow(runA);
      const reasonsB = failureReasonsForRow(runB);
      const textA = reasonsA.length ? reasonsA.map(acceptanceReasonLabel).join('、') : '已通过验收';
      const textB = reasonsB.length ? reasonsB.map(acceptanceReasonLabel).join('、') : '已通过验收';
      return `Run A：${textA}；Run B：${textB}。`;
    }

    function compareDecisionNodes(result, runA, runB, readingMeta, preferredReplayRunId, weakerRunId) {
      const betterLabel = preferredReplayRunId ? `更适合作代表回放的是 ${preferredReplayRunId}` : '当前没有明确代表 run';
      const weakerLabel = weakerRunId ? `${weakerRunId} 更适合继续做边界诊断` : '当前没有额外的边界诊断对象';
      return [
        {
          tone: 'accent',
          step: '问题',
          title: result.description || '当前实验重点是比较不同配置对闭环稳定性和控制性能的影响。',
          body: '先把对比放回研究问题，而不是只看两条曲线谁高谁低。',
        },
        {
          tone: 'ok',
          step: '结论',
          title: readingMeta.summary || '先判断谁更优',
          body: compareFailureSummary(runA, runB),
        },
        {
          tone: 'accent',
          step: '回放',
          title: betterLabel,
          body: '用代表 run 看动态过程，确认当前对比结论是不是也能在动画和时间线上成立。',
        },
        {
          tone: 'warm',
          step: '诊断',
          title: weakerLabel,
          body: '把较差 run 拉回明细、任务片段和 runtime 快照，判断问题来自参数、任务还是执行器边界。',
        },
      ];
    }

    function compareReadingWorkbenchNodes(result, runA, runB, preferredReplayRunId, weakerRunId) {
      const compareReady = Array.isArray(result.compare_run_ids) && result.compare_run_ids.length > 1;
      return [
        {
          tone: 'accent',
          step: '1. 先定问题',
          title: '对比任务书',
          body: result.description || '当前实验重点是比较不同配置对闭环稳定性和控制性能的影响。',
          action: 'brief',
          label: '看任务书',
        },
        {
          tone: 'ok',
          step: '2. 再看曲线',
          title: compareReady ? '误差与力矩图' : '关键对比入口',
          body: compareReady
            ? `重点看 ${runA?.run_id || 'Run A'} 与 ${runB?.run_id || 'Run B'} 的误差和力矩差异。`
            : '当前对比维度有限，优先看指标卡和任务书。',
          action: compareReady ? 'charts' : 'runs',
          label: compareReady ? '看曲线' : '看指标卡',
        },
        {
          tone: 'warm',
          step: '3. 最后复查',
          title: preferredReplayRunId ? `回放 ${preferredReplayRunId}` : '回到 Run 明细',
          body: weakerRunId
            ? `优先回放 ${preferredReplayRunId || '代表 run'}，并把 ${weakerRunId} 拉回 run 明细继续诊断。`
            : '当前更适合回到 Run 明细或实验链路继续复查。',
          action: 'runs',
          label: '看 Run 卡',
        },
      ];
    }

    function renderCompareView(result) {
      const runRows = new Map((result.runs || []).map(row => [row.run_id, row]));
      const compareIds = (result.compare_run_ids || []).filter(runId => runRows.has(runId));
      const histories = result.compare_histories || {};
      if (!compareIds.length) {
        if (compareQuickstartPanel) {
          compareQuickstartPanel.innerHTML = `
            <div class="history-detail" style="margin-bottom:0">
              <strong>对比入口板</strong>
              <p>当前结果还没有可对比的关键 run。建议先回结果总览或重新运行，形成最佳/最差或关键参数工况。</p>
              <div class="toolbar" style="margin-top:10px">
                <button type="button" onclick="switchResultsView('overview')">回结果总览</button>
                <button class="secondary" type="button" onclick="setResultSummaryView('roadmap')">看实验链路</button>
              </div>
            </div>
          `;
        }
        compareView.innerHTML = '<div class="empty">当前结果没有可用于对比的关键 run。</div>';
        return;
      }
      const defaultA = compareIds[0];
      const defaultB = compareIds[1] || compareIds[0];
      const compareViewMode = ['brief', 'charts', 'runs'].includes(state.compareViewMode) ? state.compareViewMode : 'brief';
      state.compareViewMode = compareViewMode;
      const compareWorkbenchView = ['focus', 'params', 'replay'].includes(state.compareWorkbenchView) ? state.compareWorkbenchView : 'focus';
      state.compareWorkbenchView = compareWorkbenchView;
      compareView.innerHTML = `
        <div class="compare-toolbar">
          <label>对比 Run A<select id="compare-a">${compareIds.map(runId => `<option value="${esc(runId)}">${esc(runId)}</option>`).join('')}</select></label>
          <label>对比 Run B<select id="compare-b">${compareIds.map(runId => `<option value="${esc(runId)}">${esc(runId)}</option>`).join('')}</select></label>
        </div>
        <div class="editor-view-shell" style="margin-bottom:12px">
          <div class="segment-control">
            <button class="${compareWorkbenchView === 'focus' ? 'active' : ''}" type="button" onclick="setCompareWorkbenchView('focus')">结论判断</button>
            <button class="${compareWorkbenchView === 'params' ? 'active' : ''}" type="button" onclick="setCompareWorkbenchView('params')">参数差异</button>
            <button class="${compareWorkbenchView === 'replay' ? 'active' : ''}" type="button" onclick="setCompareWorkbenchView('replay')">回放出口</button>
          </div>
          <div class="editor-view-note">${esc(compareWorkbenchViewNote(compareWorkbenchView))}</div>
        </div>
        <div id="compare-workbench" class="history-detail" style="margin-bottom:12px"></div>
        <div class="editor-view-shell" style="margin-bottom:12px">
          <div class="segment-control">
            <button class="${compareViewMode === 'brief' ? 'active' : ''}" type="button" onclick="switchCompareViewMode('brief')">任务书</button>
            <button class="${compareViewMode === 'charts' ? 'active' : ''}" type="button" onclick="switchCompareViewMode('charts')">曲线</button>
            <button class="${compareViewMode === 'runs' ? 'active' : ''}" type="button" onclick="switchCompareViewMode('runs')">指标卡</button>
          </div>
          <div class="editor-view-note">${esc(compareViewModeNote(compareViewMode))}</div>
        </div>
        <div class="workspace-panel-view" data-compare-view="brief" ${compareViewMode === 'brief' ? '' : 'hidden'}>
          <div id="compare-summary" class="history-detail" style="margin-bottom:12px"></div>
          <div id="compare-storyboard" class="history-detail" style="margin-bottom:12px"></div>
        </div>
        <div class="workspace-panel-view" data-compare-view="charts" ${compareViewMode === 'charts' ? '' : 'hidden'}>
          <div class="history-detail" style="margin-bottom:12px">
            <strong>关键曲线工作台</strong>
            <p>先看姿态误差，再看执行力矩。</p>
          </div>
          <svg id="compare-attitude" class="compare-chart" role="img" aria-label="姿态误差对比图"></svg>
          <svg id="compare-torque" class="compare-chart" role="img" aria-label="控制力矩对比图"></svg>
        </div>
        <div class="workspace-panel-view" data-compare-view="runs" ${compareViewMode === 'runs' ? '' : 'hidden'}>
          <div class="history-detail" style="margin-bottom:12px">
            <strong>Run 指标卡</strong>
            <p>这里适合快速确认核心指标、参数差异和验收状态。</p>
          </div>
          <div class="compare-grid">
            <div id="compare-card-a" class="compare-card"></div>
            <div id="compare-card-b" class="compare-card"></div>
          </div>
        </div>
      `;
      const selectA = document.getElementById('compare-a');
      const selectB = document.getElementById('compare-b');
      selectA.value = compareIds.includes(state.compareSelection.A) ? state.compareSelection.A : defaultA;
      selectB.value = compareIds.includes(state.compareSelection.B) ? state.compareSelection.B : defaultB;

      const update = () => {
        state.compareSelection = {A: selectA.value, B: selectB.value};
        const runA = runRows.get(selectA.value) || {};
        const runB = runRows.get(selectB.value) || {};
        const readingMeta = compareReadingMeta(result, runA, runB);
        const preferredReplayRunId = preferredReplayRunIdFromCompare(runA, runB);
        const weakerRunId = preferredReplayRunId === runA?.run_id ? runB?.run_id : runA?.run_id;
        const decisionNodes = compareDecisionNodes(result, runA, runB, readingMeta, preferredReplayRunId, weakerRunId);
        const readingNodes = compareReadingWorkbenchNodes(result, runA, runB, preferredReplayRunId, weakerRunId);
        const workbenchView = ['focus', 'params', 'replay'].includes(state.compareWorkbenchView) ? state.compareWorkbenchView : 'focus';
        state.compareWorkbenchView = workbenchView;
        const paramDiffMarkup = parameterDifferenceChips(runA, runB, {base: 'A', other: 'B'});
        const compareBinding = standardAssetBindingRecord({result});
        if (compareQuickstartPanel) {
          compareQuickstartPanel.innerHTML = `
            <div class="history-detail" style="margin-bottom:0">
              <strong>对比入口板</strong>
              <p>右侧固定保留对比结论、推荐回放对象和回主线入口，让对比页先像判读台，再像图表页。</p>
              <div class="summary-grid" style="margin-top:10px">
                <div class="summary-card"><span>Run A</span><strong>${esc(runA.run_id || '—')}</strong></div>
                <div class="summary-card"><span>Run B</span><strong>${esc(runB.run_id || '—')}</strong></div>
                <div class="summary-card"><span>当前结论</span><strong>${esc(readingMeta.summary || '待判断')}</strong></div>
                <div class="summary-card"><span>推荐回放</span><strong>${esc(preferredReplayRunId || '—')}</strong></div>
              </div>
              <div class="detail-grid" style="margin-top:10px">
                <div class="detail-box"><strong>参数差异</strong><div class="chips">${paramDiffMarkup}</div></div>
                <div class="detail-box"><strong>验收差异</strong><div>${esc(compareFailureSummary(runA, runB))}</div></div>
                <div class="detail-box"><strong>主线入口</strong><div>${esc(compareBinding?.protocol ? `${compareBinding.protocol.label} · ${compareBinding.protocol.summary}` : '当前结果还没有明确主线绑定。')}</div></div>
                <div class="detail-box"><strong>下一跳</strong><div>${esc(preferredReplayRunId ? `优先把 ${preferredReplayRunId} 送去回放，再决定是否回到计划或实验链路。` : '先看曲线和指标卡，再决定回放或回主线。')}</div></div>
              </div>
              <div class="toolbar" style="margin-top:10px">
                <button type="button" onclick="switchCompareViewMode('charts')">看曲线</button>
                <button class="secondary" type="button" onclick="${preferredReplayRunId ? `selectReplayRun('${esc(preferredReplayRunId)}')` : 'void(0)'}" ${preferredReplayRunId ? '' : 'disabled'}>送去回放</button>
                <button class="secondary" type="button" onclick="setResultSummaryView('roadmap')">回到实验链路</button>
                <button class="secondary" type="button" onclick="switchResultsView('overview')">回结果总览</button>
              </div>
            </div>
          `;
        }
        document.getElementById('compare-workbench').innerHTML = workbenchView === 'params'
          ? `
            <strong>参数差异工作台</strong>
            <p>先把差异压成参数、验收和关键指标三个层次。</p>
            <div class="detail-grid" style="margin-top:10px">
              <div class="detail-box"><strong>参数差异</strong><div class="chips">${paramDiffMarkup}</div></div>
              <div class="detail-box"><strong>验收差异</strong><div>${esc(compareFailureSummary(runA, runB))}</div></div>
              <div class="detail-box"><strong>关键指标</strong><div>Run A 末端误差 ${fmt(runA.final_error_deg)} deg / 峰值力矩 ${fmt(runA.peak_torque_nm)} N m；Run B 末端误差 ${fmt(runB.final_error_deg)} deg / 峰值力矩 ${fmt(runB.peak_torque_nm)} N m。</div></div>
              <div class="detail-box"><strong>下一动作</strong><div>${esc('差异已经清楚时看指标卡；仍不清楚时进曲线。')}</div></div>
            </div>
            <div class="toolbar" style="margin-top:12px">
              <button type="button" onclick="switchCompareViewMode('runs')">看指标卡</button>
              <button class="secondary" type="button" onclick="switchCompareViewMode('charts')">看曲线</button>
            </div>
          `
          : workbenchView === 'replay'
            ? `
              <strong>回放出口工作台</strong>
              <p>先明确更优 run 送去哪里，较差 run 去哪里继续诊断。</p>
              <div class="summary-grid" style="margin-top:10px">
                <div class="summary-card"><span>推荐回放</span><strong>${esc(preferredReplayRunId || '—')}</strong></div>
                <div class="summary-card"><span>继续诊断</span><strong>${esc(weakerRunId || '—')}</strong></div>
                <div class="summary-card"><span>当前更优</span><strong>${esc(readingMeta.summary || '待判断')}</strong></div>
                <div class="summary-card"><span>下一动作</span><strong>${esc(preferredReplayRunId ? '先回放' : '先看指标卡')}</strong></div>
              </div>
              <div class="detail-grid" style="margin-top:12px">
                <div class="detail-box"><strong>回放建议</strong><div>${esc(preferredReplayRunId ? `建议优先回放 ${preferredReplayRunId}，确认当前更优结论是否也能在动态过程和时间线上成立。` : '当前没有明确的代表回放对象，建议先看任务书和指标卡。')}</div></div>
                <div class="detail-box"><strong>诊断建议</strong><div>${esc(weakerRunId ? `建议把 ${weakerRunId} 拉回 run 明细、任务时间线和 runtime 快照继续诊断。` : '当前没有额外的边界诊断对象。')}</div></div>
                <div class="detail-box"><strong>链路出口</strong><div>${esc('形成稳定结论后，就回到实验链路或对应计划继续派生。')}</div></div>
                <div class="detail-box"><strong>快捷入口</strong><div>${esc('直接送去回放、看较差 run 明细，或回到实验链路。')}</div></div>
              </div>
              <div class="toolbar" style="margin-top:12px">
                <button type="button" onclick="${preferredReplayRunId ? `selectReplayRun('${esc(preferredReplayRunId)}')` : 'void(0)'}" ${preferredReplayRunId ? '' : 'disabled'}>把更优 Run 送去回放</button>
                <button class="secondary" type="button" onclick="${weakerRunId ? `focusRunDetail('${esc(weakerRunId)}')` : 'void(0)'}" ${weakerRunId ? '' : 'disabled'}>查看较差 Run 明细</button>
                <button class="secondary" type="button" onclick="setResultSummaryView('roadmap')">回到实验链路</button>
              </div>
            `
            : `
              <strong>结论判断工作台</strong>
              <p>先把研究问题、当前结论和判读顺序压成一屏。</p>
              <div class="flow-sequence" style="margin-top:10px">
                ${readingNodes.map(node => `
                  <div class="flow-node ${esc(node.tone)}">
                    <small>${esc(node.step)}</small>
                    <strong>${esc(node.title)}</strong>
                    <p>${esc(node.body)}</p>
                    <div class="toolbar" style="margin-top:10px">
                      <button type="button" onclick="switchCompareViewMode('${esc(node.action)}')">${esc(node.label)}</button>
                    </div>
                  </div>
                `).join('')}
              </div>
              <div class="detail-grid" style="margin-top:12px">
                <div class="detail-box"><strong>当前研究问题</strong><div>${esc(result.description || '当前实验重点是比较不同配置对闭环稳定性和控制性能的影响。')}</div></div>
                <div class="detail-box"><strong>当前结论</strong><div>${esc(readingMeta.summary)}</div></div>
                <div class="detail-box"><strong>图表提示</strong><div>姿态误差图：${esc(readingMeta.attitude)} 力矩图：${esc(readingMeta.torque)}</div></div>
                <div class="detail-box"><strong>下一步</strong><div>${esc(preferredReplayRunId ? `形成结论后可继续回放 ${preferredReplayRunId}，或拉回 ${weakerRunId || '较差 run'} 继续诊断。` : '当前更适合继续看任务书、曲线和指标卡。')}</div></div>
              </div>
              <div class="toolbar" style="margin-top:12px">
                <button type="button" onclick="switchCompareViewMode('charts')">看曲线</button>
                <button class="secondary" type="button" onclick="switchCompareViewMode('runs')">看指标卡</button>
                <button class="secondary" type="button" onclick="${preferredReplayRunId ? `selectReplayRun('${esc(preferredReplayRunId)}')` : 'void(0)'}" ${preferredReplayRunId ? '' : 'disabled'}>送去回放</button>
              </div>
            `;
        document.getElementById('compare-summary').innerHTML = `
          <strong>当前对比组合</strong>
          <div class="summary-grid" style="margin-top:10px">
            <div class="summary-card"><span>Run A</span><strong>${esc(selectA.value)}</strong></div>
            <div class="summary-card"><span>Run B</span><strong>${esc(selectB.value)}</strong></div>
            <div class="summary-card"><span>当前结论</span><strong>${esc(readingMeta.summary || '待判断')}</strong></div>
            <div class="summary-card"><span>推荐回放</span><strong>${esc(preferredReplayRunId || '—')}</strong></div>
          </div>
          <div class="chips" style="margin-top:10px">${parameterDifferenceChips(runA, runB, {base: 'A', other: 'B'})}</div>
          <div class="detail-grid" style="margin-top:10px">
            <div class="detail-box"><strong>当前结论提示</strong><div>${esc(readingMeta.summary || '待判断')}</div></div>
            <div class="detail-box"><strong>图表阅读提示</strong><div>姿态误差图：${esc(readingMeta.attitude)} 力矩图：${esc(readingMeta.torque)}</div></div>
            <div class="detail-box"><strong>最佳 Run</strong><div>${esc(result.best_run_id || '—')}</div></div>
          </div>
          <div class="toolbar" style="margin-top:10px">
            <button type="button" onclick="switchCompareViewMode('charts')">看曲线</button>
            <button class="secondary" type="button" onclick="switchCompareViewMode('runs')">看指标卡</button>
          </div>
          <strong style="display:block; margin-top:12px">首屏判读台 / 对比判读板</strong>
          <div class="flow-sequence" style="margin-top:10px">
            ${decisionNodes.map(node => `
              <div class="flow-node ${esc(node.tone)}">
                <small>${esc(node.step)}</small>
                <strong>${esc(node.title)}</strong>
                <p>${esc(node.body)}</p>
              </div>
            `).join('')}
          </div>
        `;
        document.getElementById('compare-storyboard').innerHTML = `
          <strong>对比读图任务书</strong>
          <p>先判断谁更优，再决定回放谁、继续诊断谁。</p>
          <div class="detail-grid" style="margin-top:10px">
            <div class="detail-box"><strong>当前研究问题</strong><div>${esc(result.description || '当前实验重点是比较不同配置对闭环稳定性和控制性能的影响。')}</div></div>
            <div class="detail-box"><strong>验收差异</strong><div>${esc(compareFailureSummary(runA, runB))}</div></div>
            <div class="detail-box"><strong>推荐送去回放</strong><div>${preferredReplayRunId ? `建议优先回放 ${preferredReplayRunId}，它更适合作为当前对比结论的代表动态过程。` : '当前没有明确的代表回放对象。'}</div></div>
            <div class="detail-box"><strong>建议继续诊断</strong><div>${weakerRunId ? `如果要追查边界或失败原因，建议先回看 ${weakerRunId} 的明细、任务时间线和 runtime 快照。` : '当前更适合继续看误差和力矩曲线。'}</div></div>
          </div>
          <div class="toolbar" style="margin-top:10px">
            <button type="button" onclick="${preferredReplayRunId ? `selectReplayRun('${esc(preferredReplayRunId)}')` : 'void(0)'}" ${preferredReplayRunId ? '' : 'disabled'}>把更优 Run 送去回放</button>
            <button class="secondary" type="button" onclick="${weakerRunId ? `focusRunDetail('${esc(weakerRunId)}')` : 'void(0)'}" ${weakerRunId ? '' : 'disabled'}>查看较差 Run 明细</button>
            <button class="secondary" type="button" onclick="setResultSummaryView('roadmap')">回到实验链路</button>
          </div>
        `;
        document.getElementById('compare-card-a').innerHTML = renderCompareCard(runA, 'A');
        document.getElementById('compare-card-b').innerHTML = renderCompareCard(runB, 'B');
        drawCompareChart(
          'compare-attitude',
          histories[selectA.value] || [],
          histories[selectB.value] || [],
          {
            title: '姿态误差对比',
            metricKey: 'attitude_error_deg',
            label: 'attitude_error_deg',
            colorA: '#124e78',
            colorB: '#b96a10',
          }
        );
        drawCompareChart(
          'compare-torque',
          histories[selectA.value] || [],
          histories[selectB.value] || [],
          {
            title: '执行力矩 x 对比',
            metricKey: 'applied_torque_x_nm',
            label: 'applied_torque_x_nm',
            colorA: '#0f6c7b',
            colorB: '#9f2d24',
          }
        );
        if (state.currentDashboardData?.path === result.path) {
          renderRunWorkbench(result);
          renderRunDetails(result);
        }
      };
      selectA.addEventListener('change', update);
      selectB.addEventListener('change', update);
      update();
    }

    function renderCompareCard(row, slot) {
      const paramEntries = parameterEntriesForRow(row);
      return `
        <h3>Run ${slot} · ${esc(row.run_id || '—')}</h3>
        <div class="compare-metrics">
          <div><span>末端误差</span><strong>${fmt(row.final_error_deg)} deg</strong></div>
          <div><span>RMS 误差</span><strong>${fmt(row.rms_error_deg)} deg</strong></div>
          <div><span>峰值力矩</span><strong>${fmt(row.peak_torque_nm)} N m</strong></div>
          <div><span>验收状态</span><strong>${esc(row.accepted)}</strong></div>
        </div>
        <div class="chips" style="margin-top:10px">${paramEntries.length ? paramEntries.map(([key, value]) => `<span class="chip">${esc(key)}=${esc(value)}</span>`).join('') : '<span class="subtle">暂无参数差异列</span>'}</div>
      `;
    }

    function selectCompareRun(slot, runId) {
      const select = document.getElementById(slot === 'B' ? 'compare-b' : 'compare-a');
      if (!select) return;
      if (![...select.options].some(option => option.value === runId)) return;
      select.value = runId;
      select.dispatchEvent(new Event('change'));
      compareView?.scrollIntoView({behavior: 'smooth', block: 'start'});
    }

    function selectReplayRun(runId) {
      const select = document.getElementById('replay-run');
      if (!select) return;
      if (![...select.options].some(option => option.value === runId)) return;
      select.value = runId;
      select.dispatchEvent(new Event('change'));
      replayView?.scrollIntoView({behavior: 'smooth', block: 'start'});
    }

    function drawCompareChart(id, historyA, historyB, config) {
      const svg = document.getElementById(id);
      const rows = [
        ...historyA.map(row => ({...row, __series: 'A'})),
        ...historyB.map(row => ({...row, __series: 'B'})),
      ].filter(row => Number.isFinite(Number(row.time_s)) && Number.isFinite(Number(row[config.metricKey])));
      if (!rows.length) {
        svg.innerHTML = '<text x="18" y="34" fill="#637083">暂无对比时序数据</text>';
        return;
      }
      const width = svg.clientWidth || 720;
      const height = svg.clientHeight || 220;
      const pad = {left: 48, right: 16, top: 20, bottom: 34};
      const xs = rows.map(row => Number(row.time_s));
      const ys = rows.map(row => Number(row[config.metricKey]));
      const minX = Math.min(...xs);
      const maxX = Math.max(...xs);
      const minY = Math.min(...ys, 0);
      const maxY = Math.max(...ys, 0);
      const spanX = Math.max(maxX - minX, 1e-9);
      const spanY = Math.max(maxY - minY, 1e-9);
      const plotW = width - pad.left - pad.right;
      const plotH = height - pad.top - pad.bottom;
      const xScale = x => pad.left + (x - minX) / spanX * plotW;
      const yScale = y => pad.top + plotH - (y - minY) / spanY * plotH;
      const grid = [0, .25, .5, .75, 1].map(t => {
        const y = pad.top + plotH * t;
        return `<line x1="${pad.left}" y1="${y}" x2="${width - pad.right}" y2="${y}" stroke="#d7deea" stroke-width="1"/>`;
      }).join('');
      const paths = [
        {rows: historyA, color: config.colorA, label: 'Run A'},
        {rows: historyB, color: config.colorB, label: 'Run B'},
      ].map(series => {
        const points = series.rows
          .filter(row => Number.isFinite(Number(row.time_s)) && Number.isFinite(Number(row[config.metricKey])))
          .map(row => `${xScale(Number(row.time_s))},${yScale(Number(row[config.metricKey]))}`);
        if (!points.length) return '';
        return `<polyline points="${points.join(' ')}" fill="none" stroke="${series.color}" stroke-width="2.2"><title>${series.label}</title></polyline>`;
      }).join('');
      const legend = `
        <text x="${pad.left}" y="14" fill="${config.colorA}" font-size="11">Run A</text>
        <text x="${pad.left + 78}" y="14" fill="${config.colorB}" font-size="11">Run B</text>
        <text x="${width - 180}" y="14" fill="#637083" font-size="11">${config.title}</text>
      `;
      svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
      svg.innerHTML = `${grid}<line x1="${pad.left}" y1="${pad.top + plotH}" x2="${width - pad.right}" y2="${pad.top + plotH}" stroke="#bfc9d8" stroke-width="1"/>${paths}${legend}<text x="${width - 62}" y="${height - 10}" fill="#637083" font-size="11">time_s</text><text x="8" y="30" fill="#637083" font-size="11">${fmt(maxY)}</text><text x="8" y="${height - 38}" fill="#637083" font-size="11">${fmt(minY)}</text>`;
    }

    function renderReplayView(result) {
      const histories = result.compare_histories || {};
      const runIds = (result.compare_run_ids || []).filter(runId => (histories[runId] || []).some(row => Number.isFinite(Number(row.true_qw))));
      if (!runIds.length) {
        if (replayQuickstartPanel) {
          replayQuickstartPanel.innerHTML = `
            <div class="history-detail" style="margin-bottom:0">
              <strong>回放入口板</strong>
              <p>当前结果还没有姿态四元数历史。重新运行并保留时序数据后，这里才会形成动画、任务片段和调度入口。</p>
              <div class="toolbar" style="margin-top:10px">
                <button type="button" onclick="switchResultsView('overview')">回结果总览</button>
                <button class="secondary" type="button" onclick="switchResultsView('compare')">去结果对比</button>
              </div>
            </div>
          `;
        }
        replayView.innerHTML = '<div class="empty">当前结果还没有姿态四元数历史。重新运行后即可启用三维回放。</div>';
        stopReplayAnimation();
        return;
      }
      const timeline = Array.isArray(result.timeline?.timeline) ? result.timeline.timeline : [];
      const duration = Number(result.timeline?.duration_s || 0);
      const runtime = result.runtime || {};
      const replayViewMode = ['animation', 'mission', 'runtime'].includes(state.replayViewMode) ? state.replayViewMode : 'animation';
      state.replayViewMode = replayViewMode;
      replayView.innerHTML = `
        <div class="replay-toolbar">
          <label>回放 Run<select id="replay-run">${runIds.map(runId => `<option value="${esc(runId)}">${esc(runId)}</option>`).join('')}</select></label>
          <button id="replay-play" class="secondary" type="button">播放</button>
          <button id="replay-reset" class="secondary" type="button">回到起点</button>
        </div>
        <div class="editor-view-shell" style="margin-bottom:12px">
          <div class="segment-control">
            <button class="${replayViewMode === 'animation' ? 'active' : ''}" type="button" onclick="switchReplayViewMode('animation')">动画</button>
            <button class="${replayViewMode === 'mission' ? 'active' : ''}" type="button" onclick="switchReplayViewMode('mission')">任务时间线</button>
            <button class="${replayViewMode === 'runtime' ? 'active' : ''}" type="button" onclick="switchReplayViewMode('runtime')">运行时调度</button>
          </div>
          <div class="editor-view-note">${esc(replayViewModeNote(replayViewMode))}</div>
        </div>
        <div class="editor-view-shell" style="margin-bottom:12px">
          <div class="segment-control">
            <button class="${(state.replayWorkbenchView || 'focus') === 'focus' ? 'active' : ''}" type="button" onclick="setReplayWorkbenchView('focus')">代表回放</button>
            <button class="${(state.replayWorkbenchView || 'focus') === 'mission' ? 'active' : ''}" type="button" onclick="setReplayWorkbenchView('mission')">任务片段</button>
            <button class="${(state.replayWorkbenchView || 'focus') === 'runtime' ? 'active' : ''}" type="button" onclick="setReplayWorkbenchView('runtime')">调度快照</button>
          </div>
          <div class="editor-view-note">${esc(replayWorkbenchViewNote(state.replayWorkbenchView || 'focus'))}</div>
        </div>
        <div id="replay-workbench" class="history-detail" style="margin-bottom:12px"></div>
        <div class="workspace-panel-view" data-replay-view="animation" ${replayViewMode === 'animation' ? '' : 'hidden'}>
          <div class="replay-stage">
            <div id="replay-canvas" class="replay-canvas">
              <div class="replay-world">
                <div class="replay-grid"></div>
                <div id="replay-satellite" class="satellite3d">
                  <div class="sat-body-face face-front"></div>
                  <div class="sat-body-face face-back"></div>
                  <div class="sat-body-face face-right"></div>
                  <div class="sat-body-face face-left"></div>
                  <div class="sat-body-face face-top"></div>
                  <div class="sat-body-face face-bottom"></div>
                  <div class="panel3d panel-left"></div>
                  <div class="panel3d panel-right"></div>
                  <div class="boresight3d"></div>
                </div>
              </div>
            </div>
            <div class="replay-readout">
              <div><span>仿真时间</span><strong id="replay-time">0 s</strong></div>
              <div><span>姿态误差</span><strong id="replay-error">0 deg</strong></div>
              <div><span>当前 Run</span><strong id="replay-run-label">${esc(runIds[0])}</strong></div>
              <div><span>当前模式</span><strong id="replay-mode">—</strong></div>
              <div><span>当前 Task</span><strong id="replay-task">—</strong></div>
            </div>
          </div>
          <input id="replay-slider" class="replay-slider" type="range" min="0" max="0" step="1" value="0">
        </div>
        <div class="workspace-panel-view" data-replay-view="mission" ${replayViewMode === 'mission' ? '' : 'hidden'}>
          <div class="timeline-strip" id="replay-timeline">
            <div class="timeline-legend">
              <span>任务模式时间线</span>
              <span id="replay-mode-window">${duration > 0 ? `0-${fmt(duration)} s` : '暂无 timeline'}</span>
            </div>
            <div class="timeline-bar" id="timeline-bar"></div>
          </div>
          <div class="history-detail" id="replay-timeline-cards" style="margin-top:12px"></div>
        </div>
        <div class="workspace-panel-view" data-replay-view="runtime" ${replayViewMode === 'runtime' ? '' : 'hidden'}>
          <div class="runtime-strip" id="replay-runtime">
            <h3>运行时调度</h3>
            <p id="runtime-process">${runtime.name ? esc(runtime.name) : '暂无 runtime 计划'}</p>
            <div class="runtime-modules" id="runtime-modules"></div>
          </div>
          <div class="history-detail" id="replay-runtime-track" style="margin-top:12px"></div>
        </div>
      `;
      const select = document.getElementById('replay-run');
      const slider = document.getElementById('replay-slider');
      const playButton = document.getElementById('replay-play');
      const resetButton = document.getElementById('replay-reset');
      const replayWorkbench = document.getElementById('replay-workbench');
      const replayState = {
        histories,
        runIds,
        currentRunId: runIds[0],
        frameIndex: 0,
        timeline,
        duration,
        runtimeSnapshots: Array.isArray(runtime.snapshots) ? runtime.snapshots : [],
      };
      select.value = runIds.includes(state.replayRun) ? state.replayRun : runIds[0];
      ensureReplayScene();
      renderReplayTimeline(timeline, duration);

      const renderReplayWorkbench = (sample, activeRow) => {
        if (!replayWorkbench) return;
        const workbenchView = ['focus', 'mission', 'runtime'].includes(state.replayWorkbenchView) ? state.replayWorkbenchView : 'focus';
        state.replayWorkbenchView = workbenchView;
        const currentTime = Number(sample?.time_s || 0);
        const activeMode = activeModeForTime(replayState.timeline, currentTime);
        const failureReasons = failureReasonsForRow(activeRow);
        const activeSnapshot = activeRuntimeSnapshot(replayState.runtimeSnapshots, currentTime);
        const readingNodes = replayReadingWorkbenchNodes(result, replayState);
        const replayBinding = standardAssetBindingRecord({result});
        if (replayQuickstartPanel) {
          replayQuickstartPanel.innerHTML = `
            <div class="history-detail" style="margin-bottom:0">
              <strong>回放入口板</strong>
              <p>右侧固定保留当前回放对象、时间线入口和调度入口，让回放页先像演示控制台，再进入动画细节。</p>
              <div class="summary-grid" style="margin-top:10px">
                <div class="summary-card"><span>当前 Run</span><strong>${esc(replayState.currentRunId || '—')}</strong></div>
                <div class="summary-card"><span>当前模式</span><strong>${esc(activeMode || '—')}</strong></div>
                <div class="summary-card"><span>任务片段</span><strong>${esc(replayState.timeline.length ? `${replayState.timeline.length} 段` : '暂无')}</strong></div>
                <div class="summary-card"><span>调度快照</span><strong>${esc(replayState.runtimeSnapshots.length)}</strong></div>
              </div>
              <div class="detail-grid" style="margin-top:10px">
                <div class="detail-box"><strong>动态结论</strong><div>${esc(failureReasons.length ? `当前 run 主要风险为 ${failureReasons.map(acceptanceReasonLabel).join('、')}。` : '当前 run 适合作为代表动态过程继续展示。')}</div></div>
                <div class="detail-box"><strong>回放重点</strong><div>${esc(replayState.timeline.length ? '优先看任务片段和切换段，再决定是否继续查 runtime。' : replayState.runtimeSnapshots.length ? '优先核对 runtime 快照与动画是否一致。' : '当前更适合专注姿态动画本身。')}</div></div>
                <div class="detail-box"><strong>主线入口</strong><div>${esc(replayBinding?.protocol ? `${replayBinding.protocol.label} · ${replayBinding.protocol.summary}` : '当前结果还没有明确主线绑定。')}</div></div>
                <div class="detail-box"><strong>下一跳</strong><div>${esc(replayState.timeline.length ? '看完代表动画后进入任务片段；必要时再回结果对比或计划管理。' : '当前可直接回结果总览或结果对比继续判断。')}</div></div>
              </div>
              <div class="toolbar" style="margin-top:10px">
                <button type="button" onclick="switchReplayViewMode('animation')">看动画</button>
                <button class="secondary" type="button" onclick="${replayState.timeline.length ? `setReplayWorkbenchView('mission')` : 'void(0)'}" ${replayState.timeline.length ? '' : 'disabled'}>去任务片段</button>
                <button class="secondary" type="button" onclick="${replayState.runtimeSnapshots.length ? `setReplayWorkbenchView('runtime')` : 'void(0)'}" ${replayState.runtimeSnapshots.length ? '' : 'disabled'}>去调度快照</button>
                <button class="secondary" type="button" onclick="switchResultsView('compare')">回结果对比</button>
              </div>
            </div>
          `;
        }
        if (workbenchView === 'mission') {
          const activeSegment = replayState.timeline.find(item => Number(item.start_s || 0) <= currentTime && currentTime <= Number(item.stop_s || item.start_s || 0)) || null;
          replayWorkbench.innerHTML = `
            <strong>任务片段工作台</strong>
            <p>先把当前回放放回 mission timeline 里理解。</p>
            <div class="flow-sequence" style="margin-top:10px">
              ${readingNodes.map(node => `
                <div class="flow-node ${esc(node.tone)}">
                  <small>${esc(node.step)}</small>
                  <strong>${esc(node.title)}</strong>
                  <p>${esc(node.body)}</p>
                  <div class="toolbar" style="margin-top:10px">
                    <button type="button" onclick="switchReplayViewMode('${esc(node.action)}')">${esc(node.label)}</button>
                  </div>
                </div>
              `).join('')}
            </div>
            <div class="detail-grid" style="margin-top:12px">
              <div class="detail-box"><strong>当前模式</strong><div>${esc(activeMode || '当前模式未定义')}</div></div>
              <div class="detail-box"><strong>当前片段</strong><div>${esc(activeSegment ? `${activeSegment.mode || activeSegment.name || '当前片段'} · ${fmt(activeSegment.start_s)}-${fmt(activeSegment.stop_s)} s` : '当前结果没有命中的任务片段。')}</div></div>
              <div class="detail-box"><strong>任务规模</strong><div>${esc(replayState.timeline.length ? `共 ${replayState.timeline.length} 段 mission timeline` : '当前没有 mission timeline。')}</div></div>
              <div class="detail-box"><strong>切换判断</strong><div>${esc(replayState.timeline.length ? '优先检查 detumble、hold 或 pointing 的切换段是否出现尖峰或持续偏差。' : '当前更适合看纯姿态动态过程，而不是任务切换语义。')}</div></div>
            </div>
            <div class="toolbar" style="margin-top:12px">
              <button type="button" onclick="switchReplayViewMode('mission')">看时间线</button>
              <button class="secondary" type="button" onclick="switchReplayViewMode('animation')">回动画</button>
            </div>
          `;
          return;
        }
        if (workbenchView === 'runtime') {
          replayWorkbench.innerHTML = `
            <strong>调度快照工作台</strong>
            <p>先确认当前时刻能否命中 runtime 快照。</p>
            <div class="summary-grid" style="margin-top:10px">
              <div class="summary-card"><span>快照数量</span><strong>${esc(replayState.runtimeSnapshots.length)}</strong></div>
              <div class="summary-card"><span>当前 Task</span><strong>${esc(activeSnapshot?.task || '—')}</strong></div>
              <div class="summary-card"><span>当前 Process</span><strong>${esc(activeSnapshot?.process || runtime.name || '—')}</strong></div>
              <div class="summary-card"><span>当前时刻</span><strong>${fmt(currentTime)} s</strong></div>
            </div>
            <div class="detail-grid" style="margin-top:12px">
              <div class="detail-box"><strong>快照命中</strong><div>${esc(activeSnapshot ? `已命中 ${fmt(activeSnapshot.time_s)} s 附近快照。` : '当前时刻没有命中 runtime 快照。')}</div></div>
              <div class="detail-box"><strong>模块序列</strong><div>${esc(activeSnapshot?.modules?.length ? activeSnapshot.modules.join(' -> ') : '当前快照没有模块序列。')}</div></div>
              <div class="detail-box"><strong>核对目标</strong><div>${esc(replayState.runtimeSnapshots.length ? '核对 task、modules 与 mission timeline 是否一致，避免调度语义和结果解释脱节。' : '后续带 runtime 描述的实验会在这里形成更完整的过程闭环。')}</div></div>
              <div class="detail-box"><strong>下一动作</strong><div>${esc(replayState.runtimeSnapshots.length ? '点击下方快照轨迹，把动画跳到关键时刻继续核对。' : '当前更适合切回代表回放或任务片段工作台。')}</div></div>
            </div>
            <div class="toolbar" style="margin-top:12px">
              <button type="button" onclick="switchReplayViewMode('runtime')">看调度</button>
              <button class="secondary" type="button" onclick="switchReplayViewMode('animation')">回动画</button>
            </div>
          `;
          return;
        }
        replayWorkbench.innerHTML = `
          <strong>代表回放工作台</strong>
          <p>先确认当前 run、当前模式和动态风险。</p>
          <div class="summary-grid" style="margin-top:10px">
            <div class="summary-card"><span>当前 Run</span><strong>${esc(replayState.currentRunId || '—')}</strong></div>
            <div class="summary-card"><span>姿态误差</span><strong>${fmt(sample?.attitude_error_deg)} deg</strong></div>
            <div class="summary-card"><span>当前模式</span><strong>${esc(activeMode || '—')}</strong></div>
            <div class="summary-card"><span>验收状态</span><strong>${esc(failureReasons.length ? '存在风险' : '已通过')}</strong></div>
          </div>
          <div class="callout info" style="margin-top:12px; margin-bottom:0">
            <strong>动态结论</strong>
            <p>${esc(failureReasons.length
              ? `当前 run 的主要风险是 ${failureReasons.map(acceptanceReasonLabel).join('、')}，建议继续看任务片段和调度快照确认问题发生区间。`
              : '当前回放更适合作为代表动态过程，可继续用任务片段和调度快照支撑结果展示与工程解释。')}</p>
          </div>
          <div class="detail-grid" style="margin-top:12px">
            <div class="detail-box"><strong>关键指标</strong><div>末端误差 ${fmt(activeRow?.final_error_deg)} deg，RMS ${fmt(activeRow?.rms_error_deg)} deg。</div></div>
            <div class="detail-box"><strong>任务入口</strong><div>${esc(replayState.timeline.length ? '优先切到任务片段，检查当前模式区间和切换段。' : '当前没有 mission timeline，更适合专注动画本身。')}</div></div>
            <div class="detail-box"><strong>调度入口</strong><div>${esc(replayState.runtimeSnapshots.length ? '当前已有 runtime 快照，可继续核对 task 和模块顺序。 ' : '当前没有 runtime 快照。')}</div></div>
            <div class="detail-box"><strong>下一动作</strong><div>${esc(replayState.timeline.length ? '先看任务片段，再看调度快照。' : replayState.runtimeSnapshots.length ? '先看调度快照，再回到动画确认动态语义。' : '当前回放已经足够做动态展示。')}</div></div>
          </div>
          <div class="toolbar" style="margin-top:12px">
            <button type="button" onclick="${replayState.timeline.length ? `setReplayWorkbenchView('mission')` : 'void(0)'}" ${replayState.timeline.length ? '' : 'disabled'}>去任务片段</button>
            <button class="secondary" type="button" onclick="${replayState.runtimeSnapshots.length ? `setReplayWorkbenchView('runtime')` : 'void(0)'}" ${replayState.runtimeSnapshots.length ? '' : 'disabled'}>去调度快照</button>
            <button class="secondary" type="button" onclick="switchReplayViewMode('animation')">看动画</button>
          </div>
        `;
      };

      const updateFrame = (frameIndex) => {
        const history = replayState.histories[replayState.currentRunId] || [];
        if (!history.length) return;
        replayState.frameIndex = Math.max(0, Math.min(frameIndex, history.length - 1));
        const sample = history[replayState.frameIndex];
        slider.max = String(Math.max(history.length - 1, 0));
        slider.value = String(replayState.frameIndex);
        document.getElementById('replay-time').textContent = `${fmt(sample.time_s)} s`;
        document.getElementById('replay-error').textContent = `${fmt(sample.attitude_error_deg)} deg`;
        document.getElementById('replay-run-label').textContent = replayState.currentRunId;
        document.getElementById('replay-mode').textContent = activeModeForTime(replayState.timeline, Number(sample.time_s));
        renderRuntimeSnapshot(activeRuntimeSnapshot(replayState.runtimeSnapshots, Number(sample.time_s)));
        renderReplayTimelineCards(replayState.timeline, replayState.duration, Number(sample.time_s));
        renderRuntimeSnapshotTrack(replayState.runtimeSnapshots, Number(sample.time_s));
        const activeRow = (result.runs || []).find(row => row.run_id === replayState.currentRunId) || {};
        renderReplayWorkbench(sample, activeRow);
        applyReplaySample(sample);
        updateReplayCursor(Number(sample.time_s), replayState.duration);
      };

      const updateRun = (runId) => {
        replayState.currentRunId = runId;
        state.replayRun = runId;
        const history = replayState.histories[runId] || [];
        slider.max = String(Math.max(history.length - 1, 0));
        updateFrame(0);
        if (state.currentDashboardData?.path === result.path) {
          renderRunWorkbench(result);
          renderRunDetails(result);
        }
      };
      if (window.__satmodelReplay) {
        window.__satmodelReplay.controller = {
          replayState,
          updateFrame,
        };
      }

      select.addEventListener('change', () => {
        stopReplayAnimation();
        playButton.textContent = '播放';
        updateRun(select.value);
      });
      slider.addEventListener('input', () => {
        stopReplayAnimation();
        playButton.textContent = '播放';
        updateFrame(Number(slider.value));
      });
      playButton.addEventListener('click', () => {
        if (window.__satmodelReplay?.timer) {
          stopReplayAnimation();
          playButton.textContent = '播放';
          return;
        }
        playButton.textContent = '暂停';
        window.__satmodelReplay.timer = setInterval(() => {
          const history = replayState.histories[replayState.currentRunId] || [];
          if (!history.length) return;
          const nextIndex = (replayState.frameIndex + 1) % history.length;
          updateFrame(nextIndex);
        }, 60);
      });
      resetButton.addEventListener('click', () => {
        stopReplayAnimation();
        playButton.textContent = '播放';
        updateFrame(0);
      });

      stopReplayAnimation();
      updateRun(select.value);
    }

    function ensureReplayScene() {
      const host = document.getElementById('replay-canvas');
      const satellite = document.getElementById('replay-satellite');
      if (!host || !satellite) return;
      window.__satmodelReplay = {
        host,
        satellite,
        timer: null,
      };
    }

    function applyReplaySample(sample) {
      const replay = window.__satmodelReplay;
      if (!replay?.satellite) return;
      if ([sample.true_qw, sample.true_qx, sample.true_qy, sample.true_qz].every(value => Number.isFinite(Number(value)))) {
        const [roll, pitch, yaw] = quaternionToEulerDeg(
          Number(sample.true_qw),
          Number(sample.true_qx),
          Number(sample.true_qy),
          Number(sample.true_qz),
        );
        replay.satellite.style.transform = `translate3d(-50%, -50%, 0) rotateX(${pitch}deg) rotateY(${yaw}deg) rotateZ(${roll}deg)`;
      }
    }

    function stopReplayAnimation() {
      const replay = window.__satmodelReplay;
      if (replay?.timer) {
        clearInterval(replay.timer);
        replay.timer = null;
      }
    }

    function quaternionToEulerDeg(w, x, y, z) {
      const sinr = 2 * (w * x + y * z);
      const cosr = 1 - 2 * (x * x + y * y);
      const roll = Math.atan2(sinr, cosr);
      const sinp = 2 * (w * y - z * x);
      const pitch = Math.abs(sinp) >= 1 ? Math.sign(sinp) * Math.PI / 2 : Math.asin(sinp);
      const siny = 2 * (w * z + x * y);
      const cosy = 1 - 2 * (y * y + z * z);
      const yaw = Math.atan2(siny, cosy);
      return [roll, pitch, yaw].map(value => value * 180 / Math.PI);
    }

    function renderReplayTimeline(timeline, duration) {
      const bar = document.getElementById('timeline-bar');
      if (!bar) return;
      if (!timeline.length || !(duration > 0)) {
        bar.innerHTML = '<div class="timeline-cursor" style="left:0%"></div>';
        return;
      }
      const colors = ['#124e78', '#0f6c7b', '#b96a10', '#7c9158', '#8f3a2a'];
      const segments = timeline.map((item, index) => {
        const start = Number(item.start_s || 0);
        const stop = Number(item.stop_s || start);
        const left = start / duration * 100;
        const width = Math.max((stop - start) / duration * 100, 1);
        return `<div class="timeline-segment" style="left:${left}%;width:${width}%;background:${colors[index % colors.length]}" title="${esc(item.mode || item.name || 'mode')} ${fmt(start)}-${fmt(stop)} s" onclick="jumpReplayToTime(${start})"></div>`;
      }).join('');
      bar.innerHTML = `${segments}<div id="timeline-cursor" class="timeline-cursor" style="left:0%"></div>`;
    }

    function renderReplayTimelineCards(timeline, duration, currentTime) {
      const host = document.getElementById('replay-timeline-cards');
      if (!host) return;
      if (!timeline.length || !(duration > 0)) {
        host.innerHTML = `
          <strong>任务片段卡片</strong>
          <p>当前结果没有可点击的任务模式片段。若实验包含 mission timeline，这里会给出每个模式区间的解释与跳转入口。</p>
        `;
        return;
      }
      host.innerHTML = `
        <strong>任务片段卡片</strong>
        <p>点击任一片段，就能直接把姿态回放跳到对应模式区间。这样更容易复查“切换段到底发生了什么”。</p>
        <div class="history-grid" style="margin-top:10px">
          ${timeline.map((item, index) => {
            const start = Number(item.start_s || 0);
            const stop = Number(item.stop_s || start);
            const active = start <= currentTime && currentTime <= stop;
            const durationText = Math.max(stop - start, 0);
            const label = item.mode || item.name || `segment_${index + 1}`;
            return `
              <button class="history-card ${active ? 'active' : ''}" type="button" onclick="jumpReplayToTime(${start})">
                <span>${active ? '当前片段' : `第 ${index + 1} 段`}</span>
                <strong>${esc(label)}</strong>
                <p>${fmt(start)} - ${fmt(stop)} s · 时长 ${fmt(durationText)} s<br>${active ? '当前回放时间落在这个模式区间。' : '点击后会跳到该片段起点。'}</p>
              </button>
            `;
          }).join('')}
        </div>
      `;
    }

    function renderReplayStoryboard(host, result, replayState, sample) {
      if (!host) return;
      const activeRow = (result.runs || []).find(row => row.run_id === replayState.currentRunId) || {};
      const activeMode = activeModeForTime(replayState.timeline, Number(sample?.time_s || 0));
      const failureReasons = failureReasonsForRow(activeRow);
      const storyboardNodes = [
        {
          tone: 'accent',
          step: '对象',
          title: replayState.currentRunId || '当前回放 run',
          body: `末端误差 ${fmt(activeRow.final_error_deg)} deg，RMS ${fmt(activeRow.rms_error_deg)} deg。`,
        },
        {
          tone: 'accent',
          step: '模式',
          title: activeMode || '当前模式未定义',
          body: replayState.timeline.length
            ? `当前 mission timeline 共 ${replayState.timeline.length} 段，重点看模式切换段是否平滑。`
            : '当前结果没有 mission timeline，回放更适合看纯姿态动态过程。',
        },
        {
          tone: failureReasons.length ? 'warm' : 'ok',
          step: '验收',
          title: failureReasons.length ? failureReasons.map(acceptanceReasonLabel).join('、') : '当前 run 已通过验收',
          body: failureReasons.length
            ? '结合当前时刻和任务片段卡片，判断失败是持续存在还是集中发生在切换段。'
            : '当前回放更适合作为代表动态过程，用来支撑实验结论展示。',
        },
        {
          tone: 'ok',
          step: '调度',
          title: replayState.runtimeSnapshots.length ? `${replayState.runtimeSnapshots.length} 个 runtime 快照` : '暂无 runtime 快照',
          body: replayState.runtimeSnapshots.length
            ? '点下面的快照轨迹，把动画跳到对应调度时刻，核对任务时间线和模块顺序。'
            : '若后续实验带 runtime 描述，这里会形成更完整的过程核对闭环。',
        },
      ];
      host.innerHTML = `
        <strong>回放任务板</strong>
        <p>把当前动画放回实验解释链条里理解：先确认回放对象，再看模式、验收状态和运行时调度，而不是只看卫星在转。</p>
        <div class="flow-sequence" style="margin-top:10px">
          ${storyboardNodes.map(node => `
            <div class="flow-node ${esc(node.tone)}">
              <small>${esc(node.step)}</small>
              <strong>${esc(node.title)}</strong>
              <p>${esc(node.body)}</p>
            </div>
          `).join('')}
        </div>
      `;
    }

    function replayReadingWorkbenchNodes(result, replayState) {
      const hasTimeline = replayState.timeline.length > 0;
      const hasRuntime = replayState.runtimeSnapshots.length > 0;
      return [
        {
          tone: 'accent',
          step: '1. 先看动态',
          title: '动画工作台',
          body: '先确认当前回放 run、姿态误差和整体动态过程，再判断是否需要看切换段。',
          action: 'animation',
          label: '看动画',
        },
        {
          tone: hasTimeline ? 'ok' : 'warm',
          step: '2. 再查任务',
          title: hasTimeline ? '任务模式时间线' : '任务时间线有限',
          body: hasTimeline
            ? '通过任务片段卡片和模式时间线，定位 detumble、hold 或 pointing 的关键区间。'
            : '当前结果没有完整 mission timeline，回放更适合看纯姿态动态过程。',
          action: 'mission',
          label: '看时间线',
        },
        {
          tone: hasRuntime ? 'accent' : 'warm',
          step: '3. 最后核调度',
          title: hasRuntime ? '运行时快照' : '暂无 runtime 快照',
          body: hasRuntime
            ? '把动画跳到关键调度时刻，核对 task、modules 与模式时间线是否一致。'
            : '当前没有 runtime 描述，后续带 runtime 的实验会在这里形成更完整闭环。',
          action: 'runtime',
          label: '看调度',
        },
      ];
    }

    function renderRuntimeSnapshot(snapshot) {
      const task = document.getElementById('replay-task');
      const process = document.getElementById('runtime-process');
      const modules = document.getElementById('runtime-modules');
      if (!task || !process || !modules) return;
      if (!snapshot) {
        task.textContent = '—';
        process.textContent = '当前时刻没有运行时快照';
        modules.innerHTML = '<span class="chip">暂无模块</span>';
        return;
      }
      task.textContent = snapshot.task || '—';
      process.textContent = `${snapshot.process || 'runtime'} / ${fmt(snapshot.time_s)} s`;
      modules.innerHTML = (snapshot.modules || []).map((name, index) => {
        const role = snapshot.roles?.[index];
        return `<span class="chip">${esc(name)}${role ? ` · ${esc(role)}` : ''}</span>`;
      }).join('') || '<span class="chip">暂无模块</span>';
    }

    function renderRuntimeSnapshotTrack(snapshots, currentTime) {
      const host = document.getElementById('replay-runtime-track');
      if (!host) return;
      if (!snapshots?.length) {
        host.innerHTML = `
          <strong>运行时快照轨迹</strong>
          <p>当前结果没有 runtime 快照。若实验计划带有 runtime 描述，这里会显示可点击的调度时刻序列。</p>
        `;
        return;
      }
      const rows = snapshots.slice(0, 12);
      host.innerHTML = `
        <strong>运行时快照轨迹</strong>
        <p>这里把回放和 runtime schedule 接在一起。点击任一快照，会把回放跳到该调度时刻附近，方便核对任务模式和模块序列是否一致。</p>
        <div class="history-items" style="margin-top:10px">
          ${rows.map(snapshot => {
            const timeS = Number(snapshot.time_s || 0);
            const active = Math.abs(timeS - currentTime) < 1e-6 || (timeS <= currentTime && currentTime < timeS + 1e-6);
            return `
              <button class="history-item ${active ? 'active' : ''}" type="button" onclick="jumpReplayToTime(${timeS})">
                <span class="history-time">${fmt(timeS)} s</span>
                <div>
                  <strong>${esc(snapshot.task || 'runtime task')}</strong>
                  <p>${esc(snapshot.process || 'runtime')} · ${(snapshot.modules || []).map((name, index) => `${name}${snapshot.roles?.[index] ? ` (${snapshot.roles[index]})` : ''}`).join(' -> ') || '暂无模块'}</p>
                </div>
                <span class="chip">${active ? '当前快照' : '跳转'}</span>
              </button>
            `;
          }).join('')}
        </div>
      `;
    }

    function updateReplayCursor(timeS, duration) {
      const cursor = document.getElementById('timeline-cursor');
      if (!cursor || !(duration > 0)) return;
      const left = Math.max(0, Math.min(100, Number(timeS) / duration * 100));
      cursor.style.left = `${left}%`;
      const modeWindow = document.getElementById('replay-mode-window');
      if (modeWindow) modeWindow.textContent = `${fmt(timeS)} / ${fmt(duration)} s`;
    }

    function activeModeForTime(timeline, timeS) {
      const item = (timeline || []).find(entry => Number(entry.start_s || 0) <= timeS && timeS <= Number(entry.stop_s || 0));
      return item?.mode || item?.name || '—';
    }

    function activeRuntimeSnapshot(snapshots, timeS) {
      if (!snapshots?.length) return null;
      let active = snapshots[0];
      for (const snapshot of snapshots) {
        if (Number(snapshot.time_s) <= timeS) {
          active = snapshot;
        } else {
          break;
        }
      }
      return active;
    }

    function nearestReplayFrameIndex(history, timeS) {
      if (!history?.length) return 0;
      let bestIndex = 0;
      let bestDistance = Number.POSITIVE_INFINITY;
      for (let index = 0; index < history.length; index += 1) {
        const sampleTime = Number(history[index]?.time_s);
        if (!Number.isFinite(sampleTime)) continue;
        const distance = Math.abs(sampleTime - timeS);
        if (distance < bestDistance) {
          bestDistance = distance;
          bestIndex = index;
        }
      }
      return bestIndex;
    }

    function jumpReplayToTime(timeS) {
      const replay = window.__satmodelReplay;
      if (!replay?.controller) return;
      stopReplayAnimation();
      const playButton = document.getElementById('replay-play');
      if (playButton) playButton.textContent = '播放';
      const currentRunId = replay.controller.replayState?.currentRunId;
      const history = replay.controller.replayState?.histories?.[currentRunId] || [];
      const nextIndex = nearestReplayFrameIndex(history, Number(timeS || 0));
      replay.controller.updateFrame(nextIndex);
      replayView?.scrollIntoView({behavior: 'smooth', block: 'start'});
    }

    function focusResultSection() {
      switchResultsOverviewView('focus');
      switchResultsView('overview', false);
      resultSection?.scrollIntoView({behavior: 'smooth', block: 'start'});
    }

    function focusBuilderSection() {
      switchLabView('builder', false);
      builderSection?.scrollIntoView({behavior: 'smooth', block: 'start'});
    }

    function focusEditorSection() {
      switchManageView('editor', false);
      editorSection?.scrollIntoView({behavior: 'smooth', block: 'start'});
    }

    function scrollToArchivedPlans() {
      switchManageView('history', false);
      archivedExperimentPlans?.scrollIntoView({behavior: 'smooth', block: 'start'});
    }

    function scrollToRunWorkbench() {
      switchResultsOverviewView('focus');
      switchResultsView('overview', false);
      document.getElementById('run-workbench-panel')?.scrollIntoView({behavior: 'smooth', block: 'start'});
    }

    function scrollToReplayView() {
      switchResultsView('replay', false);
      replayView?.scrollIntoView({behavior: 'smooth', block: 'start'});
    }

    function scrollToCompareView() {
      switchResultsView('compare', false);
      compareView?.scrollIntoView({behavior: 'smooth', block: 'start'});
    }

    function scrollToPreviewView() {
      switchResultsView('preview', false);
      document.getElementById('preview-shell')?.scrollIntoView({behavior: 'smooth', block: 'start'});
    }

    async function showExperiment(path, quiet = false) {
      try {
        if (!quiet) setStatus(`正在读取实验计划 ${path}...`);
        const result = await api('/api/experiment', {method:'POST', body: JSON.stringify({path})});
        renderExperimentEditor(result);
        if (!quiet) focusEditorSection();
        pushActivity('已载入实验计划', `${result.name} · 场景 ${result.scenario} · run ${result.runs}`, 'experiment');
        if (!quiet) setStatus(`已载入实验计划：${result.name}，run=${result.runs}，场景=${result.scenario}`, 'ok');
      } catch (err) {
        setStatus(err.message, 'bad');
      }
    }

    async function saveExperiment({validate = false, run = false} = {}) {
      try {
        if (!state.currentExperiment) throw new Error('还没有载入实验计划。');
        setStatus(`正在保存 ${state.currentExperiment}...`);
        const saved = await api('/api/save-experiment', {
          method:'POST',
          body: JSON.stringify({path: state.currentExperiment, text: editorText.value}),
        });
        renderExperimentEditor(saved);
        await load();
        if (validate) {
          const result = await api('/api/validate-experiment', {method:'POST', body: JSON.stringify({path: saved.path})});
          pushActivity('实验计划已保存并校验', `${result.name} · run ${result.runs} · 场景 ${result.scenario}`, 'validate');
          setStatus(`保存并校验完成：${result.name}，run=${result.runs}，场景=${result.scenario}`, 'ok');
          await showExperiment(saved.path, true);
          return saved;
        }
        if (run) {
          await runPlan(saved.path);
          await showExperiment(saved.path, true);
          return saved;
        }
        pushActivity('实验计划已保存', `${saved.name} · 输出 ${saved.path}`, 'save');
        setStatus(`已保存 ${saved.path}，run=${saved.runs}。`, 'ok');
        await showExperiment(saved.path, true);
        return saved;
      } catch (err) {
        setStatus(err.message, 'bad');
        return null;
      }
    }

    async function validatePlan(path) {
      try {
        setStatus(`正在校验 ${path}...`);
        const result = await api('/api/validate-experiment', {method:'POST', body: JSON.stringify({path})});
        pushActivity('实验计划已校验', `${result.name} · run ${result.runs} · 场景 ${result.scenario}`, 'validate');
        setStatus(`实验计划有效：${result.name}，run=${result.runs}，场景=${result.scenario}`, 'ok');
      } catch (err) {
        setStatus(err.message, 'bad');
      }
    }

    async function batchValidateExperiments() {
      const rows = selectedExperimentRows();
      if (!rows.length) {
        setStatus('还没有选择要批量校验的实验计划。', 'bad');
        return null;
      }
      try {
        const names = rows.slice(0, 3).map(plan => plan.name).join('、');
        setStatus(`正在批量校验 ${rows.length} 个计划...`);
        const results = [];
        for (const plan of rows) {
          const result = await api('/api/validate-experiment', {method:'POST', body: JSON.stringify({path: plan.path})});
          results.push(result);
        }
        pushActivity('批量校验完成', `${rows.length} 个计划已完成校验：${names}${rows.length > 3 ? ' 等' : ''}。`, 'validate');
        setStatus(`批量校验完成：${rows.length} 个计划。`, 'ok');
        return results;
      } catch (err) {
        setStatus(err.message, 'bad');
      }
    }

    async function showScenario(path, quiet = false) {
      try {
        if (!quiet) setStatus(`正在读取 ${path}...`);
        const result = await api('/api/scenario', {method:'POST', body: JSON.stringify({path})});
        renderScenarioSummary(result);
        builderScenario.value = path;
        if (!builderName.value) {
          builderName.value = `${result.name}_experiment`;
        }
        updateBuilderHints();
        pushActivity('已查看场景', `${result.name} · ${result.system}/${result.controller} · ${result.duration_s}s`, 'scenario');
        if (!quiet) setStatus(`${result.name}：${result.duration_s}s，dt=${result.dt_s}，${result.system}/${result.controller}/${result.environment}，故障=${result.fault_count}`, 'ok');
      } catch (err) {
        setStatus(err.message, 'bad');
      }
    }

    async function validateScenario(path) {
      try {
        setStatus(`正在校验 ${path}...`);
        const result = await api('/api/validate-scenario', {method:'POST', body: JSON.stringify({path})});
        renderScenarioSummary(result, true);
        pushActivity('场景已校验', `${result.name} · ${result.system}/${result.controller} · dt ${result.dt_s}s`, 'validate');
        setStatus(`场景有效：${result.name}，${result.duration_s}s，dt=${result.dt_s}，${result.system}/${result.controller}`, 'ok');
      } catch (err) {
        setStatus(err.message, 'bad');
      }
    }

    async function showDashboard(path, quiet = false) {
      try {
        if (!quiet) setStatus(`正在加载 ${path}...`);
        state.resultSummaryView = 'overview';
        const result = await api('/api/dashboard', {method:'POST', body: JSON.stringify({path})});
        renderDashboardSummary(result);
        if (!quiet) focusResultSection();
        pushActivity('已查看结果', `${result.experiment_name} · run ${result.run_count} · 最佳 ${result.best_run_id || '—'}`, 'result');
        if (!quiet) setStatus(`已加载结果：${result.experiment_name}，run=${result.run_count}，最佳=${result.best_run_id || '—'}`, 'ok');
      } catch (err) {
        setStatus(err.message, 'bad');
      }
    }

    async function runPlan(path, options = {}) {
      try {
        const matchedPlan = [...(state.workspace?.experiments || [])].find(plan => plan.path === path);
        const resolvedOutputDir = options.outputDir || output.value.trim() || '';
        state.runAction = {
          mode: state.currentDashboard && matchedPlan && matchingDashboardForPlan(matchedPlan)?.path === state.currentDashboard ? 'rerun' : 'run',
          path,
          planName: matchedPlan?.name || path,
          outputDir: resolvedOutputDir,
          startedAt: Date.now(),
        };
        renderRunStatusPanels();
        setStatus(`正在运行 ${path}...`);
        const body = {path};
        if (options.outputDir) {
          body.output_dir = options.outputDir;
        } else if (output.value.trim()) {
          body.output_dir = output.value.trim();
        }
        const result = await api('/api/run-experiment', {method:'POST', body: JSON.stringify(body)});
        state.runAction = null;
        state.latestRunDashboard = result.dashboard;
        if (result.summary) {
          state.latestRunSummary = {...result.summary};
        }
        if (state.builderLastCreated?.path === path) {
          state.builderLastCreated = {...state.builderLastCreated, dashboard: result.dashboard};
          renderBuilderResult();
        }
        await load({preserveSelection: false});
        if (result.summary) {
          renderDashboardSummary(result.summary);
        } else {
          await showDashboard(result.dashboard, true);
        }
        focusResultSection();
        renderRunStatusPanels();
        pushActivity('实验已运行完成', `${path} · ${result.runs} 个 run · 通过 ${result.accepted} / 失败 ${result.failed}`, 'run');
        setStatus(`完成：${result.runs} 个 run，通过=${result.accepted}，失败=${result.failed}。`, 'ok');
        return result;
      } catch (err) {
        state.runAction = null;
        renderRunStatusPanels();
        setStatus(err.message, 'bad');
      }
    }

    async function createPlan(options = {}) {
      const runAfterCreate = Boolean(
        options
        && typeof options === 'object'
        && !('preventDefault' in options)
        && options.runAfterCreate
      );
      const quickDemo = options && typeof options === 'object' && !('preventDefault' in options)
        ? options.quickDemo || null
        : null;
      try {
        const scenario = builderScenario.value;
        if (!scenario) throw new Error('还没有选择场景。');
        state.builderError = '';
        const body = {
          scenario_path: scenario,
          name: builderName.value || 'generated_experiment',
          description: builderDescription.value,
          output_root: builderOutput.value,
          sweep_path: builderSweepPath.value,
          sweep_values: builderSweepValues.value,
          second_sweep_path: builderSecondSweepPath.value,
          second_sweep_values: builderSecondSweepValues.value,
          monte_carlo_samples: Number(builderMcSamples.value || 0),
          monte_carlo_seed: builderMcSeed.value,
          mission_template: builderMission.value,
          mode: builderMode.value,
          hold_mode: builderMode.value,
          detumble_s: Number(builderDetumble.value || 0.5),
          reference: builderReference.value,
          acceptance_preset: builderAcceptancePreset.value,
          acceptance_final_deg: builderAcceptFinal.value,
          acceptance_rms_deg: builderAcceptRms.value,
          acceptance_peak_torque_nm: builderAcceptTorque.value,
        };
        state.builderAction = {
          mode: runAfterCreate ? 'run' : 'create',
          name: body.name,
        };
        if (quickDemo) {
          state.quickDemoSession = {
            ...(state.quickDemoSession || {}),
            demoId: state.quickDemoSession?.demoId || quickDemo.label,
            label: quickDemo.label,
            description: quickDemo.description,
            status: runAfterCreate ? 'creating' : 'prepared',
            outputRoot: body.output_root || builderOutput.value,
          };
          renderQuickDemoStatus();
        }
        renderBuilderResult();
        syncCreateButtons();
        focusBuilderSection();
        setStatus(`正在创建 ${body.name}...`);
        const result = await api('/api/create-experiment', {method:'POST', body: JSON.stringify(body)});
        state.builderAction = null;
        state.builderLastCreated = {...result};
        if (quickDemo || state.quickDemoSession?.status) {
          state.quickDemoSession = {
            ...(state.quickDemoSession || {}),
            label: quickDemo?.label || state.quickDemoSession?.label,
            description: quickDemo?.description || state.quickDemoSession?.description,
            status: runAfterCreate ? 'running' : 'prepared',
            planPath: result.path,
            outputRoot: result.output_root,
          };
          renderQuickDemoStatus();
        }
        builderName.value = result.name || body.name;
        builderOutput.value = result.output_root || builderOutput.value;
        updateBuilderHints();
        renderBuilderResult();
        await load();
        await showExperiment(result.path, true);
        focusEditorSection();
        if (runAfterCreate) {
          pushActivity(
            '实验计划已创建',
            result.resolved_from_collision
              ? `${result.requested_name} 已自动保存为 ${result.name} · 输出 ${result.output_root}`
              : `${result.name} · 输出 ${result.output_root}`,
            'create'
          );
          state.builderAction = {mode: 'run', name: result.name};
          renderBuilderResult();
          syncCreateButtons();
          const runResult = await runPlan(result.path, {outputDir: result.output_root});
          state.builderAction = null;
          if (runResult) {
            state.builderLastCreated = {...state.builderLastCreated, dashboard: runResult.dashboard};
            if (state.quickDemoSession) {
              state.quickDemoSession = {
                ...(state.quickDemoSession || {}),
                status: 'completed',
                dashboardPath: runResult.dashboard,
                runCount: runResult.runs,
                accepted: runResult.accepted,
                failed: runResult.failed,
                bestFinalErrorDeg: runResult.best_final_error_deg,
                outputRoot: runResult.output_dir,
              };
              renderQuickDemoStatus();
            }
            renderBuilderResult();
            syncCreateButtons();
            focusResultSection();
          } else {
            if (state.quickDemoSession) {
              state.quickDemoSession = {
                ...(state.quickDemoSession || {}),
                status: 'error',
              };
              renderQuickDemoStatus();
            }
            state.builderError = '实验计划已经创建，但自动运行没有成功完成。你可以先查看计划，再决定是否重新运行。';
            renderBuilderResult();
            syncCreateButtons();
            focusBuilderSection();
          }
          return;
        }
        pushActivity(
          '实验计划已创建',
          result.resolved_from_collision
            ? `${result.requested_name} 已自动保存为 ${result.name} · 输出 ${result.output_root}`
            : `${result.name} · 输出 ${result.output_root}`,
          'create'
        );
        setStatus(
          result.resolved_from_collision
            ? `已创建 ${result.path}，原名称重复，已自动改为 ${result.name}；校验 run=${result.validation.runs}。`
            : `已创建 ${result.path}；校验 run=${result.validation.runs}。`,
          'ok'
        );
        syncCreateButtons();
      } catch (err) {
        if (state.quickDemoSession && (quickDemo || state.quickDemoSession.status === 'creating' || state.quickDemoSession.status === 'running')) {
          state.quickDemoSession = {
            ...(state.quickDemoSession || {}),
            status: 'error',
          };
          renderQuickDemoStatus();
        }
        state.builderAction = null;
        state.builderError = err.message;
        renderBuilderResult();
        syncCreateButtons();
        focusBuilderSection();
        setStatus(err.message, 'bad');
      }
    }

    async function duplicateExperiment(path) {
      try {
        const matchedPlan = [...(state.workspace?.experiments || [])].find(plan => plan.path === path);
        const defaultName = `${matchedPlan?.name || state.currentExperimentMapping?.metadata?.name || 'experiment'}_copy`;
        const requestedName = window.prompt('请输入副本计划名称', defaultName);
        if (requestedName === null) {
          setStatus('已取消复制实验计划。');
          return null;
        }
        const trimmedName = requestedName.trim();
        if (!trimmedName) {
          throw new Error('副本计划名称不能为空。');
        }
        state.builderError = '';
        renderBuilderResult();
        setStatus(`正在复制 ${path}...`);
        const result = await api('/api/duplicate-experiment', {
          method:'POST',
          body: JSON.stringify({path, name: trimmedName}),
        });
        state.builderLastCreated = {...result};
        renderBuilderResult();
        await load();
        await showExperiment(result.path, true);
        focusEditorSection();
        pushActivity(
          '实验计划已复制',
          result.resolved_from_collision
            ? `${result.requested_name} 已自动保存为 ${result.name} · 基于 ${result.source_path}`
            : `${result.name} · 基于 ${result.source_path}`,
          'save'
        );
        setStatus(
          result.resolved_from_collision
            ? `已复制为 ${result.path}，原名称重复，已自动改为 ${result.name}；校验 run=${result.validation.runs}。`
            : `已复制为 ${result.path}；校验 run=${result.validation.runs}。`,
          'ok'
        );
        return result;
      } catch (err) {
        setStatus(err.message, 'bad');
      }
    }

    async function renameExperiment(path) {
      try {
        let targetPath = path;
        const matchedPlan = [...(state.workspace?.experiments || [])].find(plan => plan.path === path);
        const defaultName = matchedPlan?.name || state.currentExperimentMapping?.metadata?.name || 'experiment';
        const requestedName = window.prompt('请输入新的实验计划名称', defaultName);
        if (requestedName === null) {
          setStatus('已取消重命名实验计划。');
          return null;
        }
        const trimmedName = requestedName.trim();
        if (!trimmedName) {
          throw new Error('新的实验计划名称不能为空。');
        }
        if (state.currentExperiment === path && state.currentExperimentDirty) {
          const shouldSave = window.confirm('当前计划有未保存修改。是否先保存再重命名？');
          if (!shouldSave) {
            setStatus('已取消重命名实验计划。');
            return null;
          }
          const saved = await saveExperiment();
          if (!saved) {
            return null;
          }
          targetPath = saved.path;
        }
        setStatus(`正在重命名 ${targetPath}...`);
        const result = await api('/api/rename-experiment', {
          method:'POST',
          body: JSON.stringify({path: targetPath, name: trimmedName}),
        });
        await load();
        await showExperiment(result.path, true);
        focusEditorSection();
        pushActivity(
          '实验计划已重命名',
          result.resolved_from_collision
            ? `${result.previous_name} 已重命名为 ${result.name}，原目标名称冲突后自动调整。`
            : `${result.previous_name} 已重命名为 ${result.name}。`,
          'save'
        );
        setStatus(
          result.resolved_from_collision
            ? `已重命名为 ${result.path}，目标名称冲突后自动改为 ${result.name}；校验 run=${result.validation.runs}。`
            : `已重命名为 ${result.path}；校验 run=${result.validation.runs}。`,
          'ok'
        );
        return result;
      } catch (err) {
        setStatus(err.message, 'bad');
      }
    }

    async function archiveExperiment(path) {
      try {
        let targetPath = path;
        const matchedPlan = [...(state.workspace?.experiments || [])].find(plan => plan.path === path);
        const planName = matchedPlan?.name || state.currentExperimentMapping?.metadata?.name || path;
        const confirmed = window.confirm(`确认将实验计划“${planName}”归档到 scenarios/archive/ 吗？归档后它会从活动列表中移除。`);
        if (!confirmed) {
          setStatus('已取消归档实验计划。');
          return null;
        }
        if (state.currentExperiment === path && state.currentExperimentDirty) {
          const shouldSave = window.confirm('当前计划有未保存修改。是否先保存再归档？');
          if (!shouldSave) {
            setStatus('已取消归档实验计划。');
            return null;
          }
          const saved = await saveExperiment();
          if (!saved) {
            return null;
          }
          targetPath = saved.path;
        }
        setStatus(`正在归档 ${targetPath}...`);
        const result = await api('/api/archive-experiment', {
          method:'POST',
          body: JSON.stringify({path: targetPath}),
        });
        if (state.currentExperiment === targetPath) {
          renderExperimentEditor(null);
        }
        await load({preserveSelection: false});
        pushActivity(
          '实验计划已归档',
          `${result.name} 已从 ${result.source_path} 移动到 ${result.archived_path}。`,
          'save'
        );
        setStatus(`已归档到 ${result.archived_path}。`, 'ok');
        return result;
      } catch (err) {
        setStatus(err.message, 'bad');
      }
    }

    async function batchArchiveExperiments() {
      const rows = selectedExperimentRows();
      if (!rows.length) {
        setStatus('还没有选择要批量归档的实验计划。', 'bad');
        return null;
      }
      try {
        const confirmed = window.confirm(`确认将这 ${rows.length} 个实验计划批量归档到 scenarios/archive/ 吗？`);
        if (!confirmed) {
          setStatus('已取消批量归档实验计划。');
          return null;
        }
        if (state.currentExperimentDirty && rows.some(plan => plan.path === state.currentExperiment)) {
          const shouldSave = window.confirm('当前编辑中的计划也在批量归档列表里。是否先保存再继续？');
          if (!shouldSave) {
            setStatus('已取消批量归档实验计划。');
            return null;
          }
          const saved = await saveExperiment();
          if (!saved) {
            return null;
          }
        }
        setStatus(`正在批量归档 ${rows.length} 个计划...`);
        const archived = [];
        for (const plan of rows) {
          const result = await api('/api/archive-experiment', {
            method:'POST',
            body: JSON.stringify({path: plan.path}),
          });
          archived.push(result);
        }
        if (rows.some(plan => plan.path === state.currentExperiment)) {
          renderExperimentEditor(null);
        }
        state.selectedExperiments = [];
        await load({preserveSelection: false});
        pushActivity('批量归档完成', `${archived.length} 个计划已移动到 scenarios/archive/。`, 'save');
        setStatus(`批量归档完成：${archived.length} 个计划。`, 'ok');
        return archived;
      } catch (err) {
        setStatus(err.message, 'bad');
      }
    }

    async function restoreExperiment(path) {
      try {
        const archivedPlan = [...(state.workspace?.archived_experiments || [])].find(plan => plan.path === path);
        const planName = archivedPlan?.name || path;
        const confirmed = window.confirm(`确认恢复归档计划“${planName}”到活动实验列表吗？`);
        if (!confirmed) {
          setStatus('已取消恢复实验计划。');
          return null;
        }
        setStatus(`正在恢复 ${path}...`);
        const result = await api('/api/restore-experiment', {
          method:'POST',
          body: JSON.stringify({path}),
        });
        state.builderLastCreated = {...result};
        renderBuilderResult();
        await load({preserveSelection: false});
        await showExperiment(result.path, true);
        focusEditorSection();
        pushActivity(
          '归档计划已恢复',
          result.resolved_from_collision
            ? `${result.source_path} 已恢复为 ${result.path}，名称冲突后自动调整为 ${result.name}。`
            : `${result.source_path} 已恢复为 ${result.path}。`,
          'save'
        );
        setStatus(
          result.resolved_from_collision
            ? `已恢复到 ${result.path}，名称冲突后自动改为 ${result.name}；校验 run=${result.validation.runs}。`
            : `已恢复到 ${result.path}；校验 run=${result.validation.runs}。`,
          'ok'
        );
        return result;
      } catch (err) {
        setStatus(err.message, 'bad');
      }
    }

    openDashboard.addEventListener('click', () => {
      if (state.currentDashboardUrl) window.open(state.currentDashboardUrl, '_blank');
    });
    builderResultOpenPlan.addEventListener('click', () => {
      if (state.builderLastCreated?.path) showExperiment(state.builderLastCreated.path);
    });
    builderResultRun.addEventListener('click', () => {
      if (state.builderLastCreated?.path) runPlan(state.builderLastCreated.path, {outputDir: state.builderLastCreated.output_root});
    });
    builderResultOpenResult.addEventListener('click', () => {
      if (state.builderLastCreated?.dashboard) showDashboard(state.builderLastCreated.dashboard);
    });
    editorText.addEventListener('input', () => {
      state.currentExperimentDirty = true;
      try {
        const mapping = JSON.parse(editorText.value);
        state.currentExperimentMapping = mapping;
        refreshEditorOverviewFromMapping();
        renderEditorPlanContext(activePlanRecord());
      } catch {
        editorOverview.innerHTML = '<div class="callout warning" style="margin-bottom:0"><strong>概览暂不可用</strong><p>当前 JSON 还不能解析，先修正格式后就会自动恢复结构概览。</p></div>';
      }
      updateEditorButtons();
    });
    Object.assign(window, {
      archiveExperiment,
      batchArchiveExperiments,
      batchValidateExperiments,
      clearSelectedExperiments,
      duplicateExperiment,
      focusRunDetail,
      jumpReplayToTime,
      loadSelectedEditorExperiment,
      openSelectedEditorExperimentResult,
      prepareQuickExample,
      previewRoadmapExperiment,
      renameExperiment,
      resetEditorStructuredGroup,
      restoreExperiment,
      runPlan,
      runQuickExample,
      runSelectedEditorExperiment,
      scrollToArchivedPlans,
      scrollToCompareView,
      scrollToPreviewView,
      scrollToReplayView,
      scrollToRunWorkbench,
      selectLibraryExperiment,
      selectLibraryProtocol,
        setCompareWorkbenchView,
        setDashboardLineFilter,
        setEditorContextView,
        setLibraryCategory,
        setLibraryDetailView,
        switchEditorWorkbenchView,
        setReplayWorkbenchView,
        setResultSummaryView,
        setResultWorkbenchView,
      showDashboard,
      showExperiment,
      showScenario,
      switchCompareViewMode,
      switchLabView,
      switchManageCockpitView,
      switchManagePoolView,
      switchManageView,
      switchManageWorkbenchView,
      switchPreviewViewMode,
      switchReplayViewMode,
      switchResultsCockpitView,
      switchResultsOverviewView,
      switchResultsView,
      validatePlan,
      validateScenario,
    });

    editorLoad.addEventListener('click', () => state.currentExperiment && showExperiment(state.currentExperiment));
    editorSave.addEventListener('click', () => saveExperiment());
    editorValidate.addEventListener('click', () => saveExperiment({validate: true}));
    editorRun.addEventListener('click', () => saveExperiment({run: true}));
    editorDuplicate.addEventListener('click', () => state.currentExperiment && duplicateExperiment(state.currentExperiment));
    editorRename.addEventListener('click', () => state.currentExperiment && renameExperiment(state.currentExperiment));
    editorArchive.addEventListener('click', () => state.currentExperiment && archiveExperiment(state.currentExperiment));
    builderTemplateGrid?.querySelectorAll('.template-card').forEach(card => {
      card.addEventListener('click', () => applyExperimentTemplate(card.dataset.template));
    });
    editorWorkbenchToggle?.querySelectorAll('[data-editor-workbench-view]').forEach(button => {
      button.addEventListener('click', () => switchEditorWorkbenchView(button.dataset.editorWorkbenchView || 'context'));
    });
    builderCategoryNav?.querySelectorAll('[data-builder-category]').forEach(button => {
      button.addEventListener('click', () => setBuilderCategory(button.dataset.builderCategory || 'all'));
    });
    builderSweepPreset.addEventListener('change', () => {
      const config = sweepPresetConfig(builderSweepPreset.value || 'custom');
      if (config.path) {
        builderSweepPath.value = config.path;
      }
      renderSweepValuePresets();
      if (config.valuePresets?.length) {
        builderSweepValuesPreset.value = config.valuePresets[0].values;
        builderSweepValues.value = config.valuePresets[0].values;
      } else {
        builderSweepValuesPreset.value = 'custom';
      }
      updateBuilderHints();
    });
    builderSecondSweepPreset.addEventListener('change', () => {
      const config = sweepPresetConfig(builderSecondSweepPreset.value || 'custom');
      builderSecondSweepPath.value = config.path || '';
      renderSecondSweepValuePresets();
      if (config.valuePresets?.length) {
        builderSecondSweepValuesPreset.value = config.valuePresets[0].values;
        builderSecondSweepValues.value = config.valuePresets[0].values;
      } else {
        builderSecondSweepValuesPreset.value = 'custom';
        if (builderSecondSweepPreset.value === 'custom') {
          builderSecondSweepValues.value = '';
        }
      }
      updateBuilderHints();
    });
    builderSweepValuesPreset.addEventListener('change', () => {
      if (builderSweepValuesPreset.value !== 'custom') {
        builderSweepValues.value = builderSweepValuesPreset.value;
      }
      updateBuilderHints();
    });
    builderSecondSweepValuesPreset.addEventListener('change', () => {
      if (builderSecondSweepValuesPreset.value !== 'custom') {
        builderSecondSweepValues.value = builderSecondSweepValuesPreset.value;
      }
      updateBuilderHints();
    });
    [
      builderScenario,
      builderName,
      builderOutput,
      builderSweepValues,
      builderSecondSweepValues,
      builderMcSamples,
      builderMcSeed,
      builderMission,
      builderMode,
      builderDetumble,
      builderDescription,
      builderReference,
      builderAcceptFinal,
      builderAcceptRms,
      builderAcceptTorque,
    ].forEach(node => node.addEventListener('input', updateBuilderHints));
    builderSweepPath.addEventListener('input', () => {
      builderSweepPreset.value = knownSweepPresetFromPath(builderSweepPath.value);
      renderSweepValuePresets();
      updateBuilderHints();
    });
    builderSecondSweepPath.addEventListener('input', () => {
      builderSecondSweepPreset.value = knownSweepPresetFromPath(builderSecondSweepPath.value);
      renderSecondSweepValuePresets();
      updateBuilderHints();
    });
    builderSweepValues.addEventListener('input', () => {
      builderSweepValuesPreset.value = 'custom';
      updateBuilderHints();
    });
    builderSecondSweepValues.addEventListener('input', () => {
      builderSecondSweepValuesPreset.value = 'custom';
      updateBuilderHints();
    });
    builderMission.addEventListener('change', updateBuilderHints);
    builderMode.addEventListener('change', updateBuilderHints);
    builderScenario.addEventListener('change', updateBuilderHints);
    builderAcceptancePreset.addEventListener('change', () => {
      applyAcceptancePreset(builderAcceptancePreset.value, {force: builderAcceptancePreset.value !== 'custom'});
      updateBuilderHints();
    });
    dashboardFilter.addEventListener('input', () => {
      state.dashboardFilter = dashboardFilter.value;
      if (state.workspace?.dashboards) renderDashboards();
    });
    experimentFilter.addEventListener('input', () => {
      state.experimentFilter = experimentFilter.value;
      if (state.workspace?.experiments) {
        renderExperiments();
        renderExperimentListSwitcher();
        renderExperimentBatchBar();
        renderRecentExperimentPlans();
        renderManagePoolCockpit();
        renderManageWorkbenchSummary();
      }
    });
    experimentStatusFilter.addEventListener('change', () => {
      state.experimentStatusFilter = experimentStatusFilter.value;
      if (state.workspace?.experiments) {
        renderExperiments();
        renderExperimentListSwitcher();
        renderExperimentBatchBar();
        renderRecentExperimentPlans();
        renderManagePoolCockpit();
        renderManageWorkbenchSummary();
      }
    });
    dashboardStatusFilter.addEventListener('change', () => {
      state.dashboardStatusFilter = dashboardStatusFilter.value;
      if (state.workspace?.dashboards) renderDashboards();
    });
    dashboardLineFilter?.addEventListener('change', () => {
      state.dashboardLineFilter = dashboardLineFilter.value || 'all';
      if (state.workspace?.dashboards) renderDashboards();
    });
    document.getElementById('refresh').addEventListener('click', () => load());
    manageRefreshTopButton?.addEventListener('click', () => load());
    manageOpenLibraryButton?.addEventListener('click', () => switchLabView('library'));
    manageOpenBuilderButton?.addEventListener('click', () => switchLabView('builder'));
    document.getElementById('create-plan').addEventListener('click', () => createPlan());
    document.getElementById('create-plan-run').addEventListener('click', () => createPlan({runAfterCreate: true}));
    bindPrimaryNav(sidebarNav, button => {
      navigateTo(button.dataset.page || 'overview', {
        labView: button.dataset.labView,
        resultsView: button.dataset.resultsView,
      });
    });
    bindPrimaryNav(pageNav, button => {
      navigateTo(button.dataset.page || 'overview', {
        labView: button.dataset.labView,
        resultsView: button.dataset.resultsView,
      });
    });
    sidebarNav?.querySelectorAll('[data-page]').forEach(button => {
      if (button.tagName === 'A') return;
      button.addEventListener('click', event => {
        event.preventDefault();
        navigateTo(button.dataset.page || 'overview', {
          labView: button.dataset.labView,
          resultsView: button.dataset.resultsView,
        });
      });
    });
    pageNav?.querySelectorAll('[data-page]').forEach(button => {
      if (button.tagName === 'A') return;
      button.addEventListener('click', event => {
        event.preventDefault();
        navigateTo(button.dataset.page || 'overview', {
          labView: button.dataset.labView,
          resultsView: button.dataset.resultsView,
        });
      });
    });
    labNav?.querySelectorAll('[data-lab-view]').forEach(button => {
      button.addEventListener('click', () => switchLabView(button.dataset.labView || 'library'));
    });
    libraryWorkspaceToggle?.querySelectorAll('[data-library-view]').forEach(button => {
      button.addEventListener('click', () => switchLibraryView(button.dataset.libraryView || 'route'));
    });
    manageNav?.querySelectorAll('[data-manage-view]').forEach(button => {
      button.addEventListener('click', () => switchManageView(button.dataset.manageView || 'pool'));
    });
    managePoolToggle?.querySelectorAll('[data-manage-pool-view]').forEach(button => {
      button.addEventListener('click', () => switchManagePoolView(button.dataset.managePoolView || 'focus'));
    });
    resultsNav?.querySelectorAll('[data-results-view]').forEach(button => {
      button.addEventListener('click', () => switchResultsView(button.dataset.resultsView || 'overview'));
    });
    resultsOverviewToggle?.querySelectorAll('[data-results-overview-view]').forEach(button => {
      button.addEventListener('click', () => switchResultsOverviewView(button.dataset.resultsOverviewView || 'cockpit'));
    });
    resultsBrowserToggle?.querySelectorAll('[data-results-browser-view]').forEach(button => {
      button.addEventListener('click', () => setResultsBrowserView(button.dataset.resultsBrowserView || 'representative'));
    });
    overviewWorkbenchToggle?.querySelectorAll('[data-overview-workbench-view]').forEach(button => {
      button.addEventListener('click', () => setOverviewWorkbenchView(button.dataset.overviewWorkbenchView || 'position'));
    });
    builderViewToggle?.querySelectorAll('button').forEach(button => {
      button.addEventListener('click', () => {
        state.builderViewMode = button.dataset.builderView || 'basic';
        renderBuilderViewMode();
      });
    });
    builderStageNav?.querySelectorAll('[data-builder-stage-target]').forEach(button => {
      button.addEventListener('click', () => switchBuilderStage(button.dataset.builderStageTarget || 'question'));
    });
    builderWorkbenchToggle?.querySelectorAll('[data-builder-workbench-view]').forEach(button => {
      button.addEventListener('click', () => switchBuilderWorkbenchView(button.dataset.builderWorkbenchView || 'focus'));
    });
    editorViewToggle?.querySelectorAll('button').forEach(button => {
      button.addEventListener('click', () => {
        state.editorViewMode = button.dataset.editorView || 'overview';
        renderEditorViewMode();
        syncRouteHash();
      });
    });
    window.addEventListener('hashchange', () => {
      if (routeSyncLocked) return;
      hydrateStateFromHash();
      renderRouteState();
    });
    hydrateStateFromHash();
    renderRouteState();
    syncRouteHash();
    window.__satmodelAppReady = true;
    load().catch(err => setStatus(err.message, 'bad'));
      } catch (err) {
        const statusNode = document.getElementById('status');
        if (statusNode) {
          statusNode.className = 'status bad';
          statusNode.textContent = `前端初始化失败：${err.message}`;
        }
        console.error(err);
      }
    })();
  </script>
</body>
</html>
"""
    return html.replace("__UI_BUILD_STAMP__", build_stamp).replace("__SATMODEL_VERSION__", __version__)
