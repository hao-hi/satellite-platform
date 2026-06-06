"""Local browser UI for operating satmodel platform experiments."""

from __future__ import annotations

import csv
import json
import mimetypes
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
from satmodel.platform.plan import load_experiment_plan
from satmodel.platform.runner import ExperimentRunner


def discover_workspace(root: str | Path) -> dict[str, Any]:
    """Discover scenario files, experiment plans, and result dashboards."""

    workspace = Path(root).resolve()
    scenario_dir = workspace / "scenarios"
    results_dir = workspace / "results"
    scenarios: list[dict[str, Any]] = []
    experiments: list[dict[str, Any]] = []
    for path in sorted(scenario_dir.glob("*.json")) if scenario_dir.exists() else []:
        rel = _relative(path, workspace)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "scenario" in data:
                plan = load_experiment_plan(path)
                experiments.append(
                    {
                        "name": plan.name,
                        "path": rel,
                        "scenario": plan.scenario.metadata.name,
                        "sweeps": len(plan.sweeps),
                        "monte_carlo_samples": 0 if plan.monte_carlo is None else plan.monte_carlo.samples,
                    }
                )
            else:
                scenarios.append(
                    {
                        "name": data.get("metadata", {}).get("name", path.stem) if isinstance(data, dict) else path.stem,
                        "path": rel,
                        "duration_s": data.get("time", {}).get("duration_s") if isinstance(data, dict) else None,
                        "dt_s": data.get("time", {}).get("dt_s") if isinstance(data, dict) else None,
                        "system": data.get("system", {}).get("builder") if isinstance(data, dict) else None,
                        "controller": data.get("system", {}).get("controller") if isinstance(data, dict) else None,
                    }
                )
        except Exception as exc:
            experiments.append({"name": path.stem, "path": rel, "error": str(exc)})
    dashboards = []
    for path in sorted(results_dir.rglob("dashboard.html")) if results_dir.exists() else []:
        dashboards.append(_dashboard_listing(workspace, path))
    return {
        "workspace": str(workspace),
        "satmodel_version": __version__,
        "scenarios": scenarios,
        "experiments": experiments,
        "dashboards": dashboards,
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
        "runs": len(cases),
        "scenario": plan.scenario.metadata.name,
        "sweeps": len(plan.sweeps),
        "monte_carlo_samples": 0 if plan.monte_carlo is None else plan.monte_carlo.samples,
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
    name = str(payload.get("name") or scenario_path.stem).strip()
    if not name:
        raise ValueError("experiment name is required")
    slug = _slug(name)
    scenario_dir = workspace / "scenarios"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    plan_path = scenario_dir / f"{slug}.json"
    if plan_path.exists() and not payload.get("overwrite", False):
        raise ValueError(f"experiment plan already exists: {_relative(plan_path, workspace)}")

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
            "root": str(payload.get("output_root") or f"results/platform_ui/{slug}"),
        },
        "runtime": {"template": "single_rate"},
    }

    sweep_path = str(payload.get("sweep_path") or "").strip()
    sweep_values = _parse_list(payload.get("sweep_values"))
    if sweep_path and sweep_values:
        plan["sweeps"].append({"path": sweep_path, "values": sweep_values})
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

    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    validation = validate_workspace_experiment(workspace, _relative(plan_path, workspace))
    return {
        "path": _relative(plan_path, workspace),
        "name": name,
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
            if parsed.path == "/api/dashboard":
                self._send_json(describe_workspace_dashboard(self.workspace_root, payload["path"]))
                return
            if parsed.path == "/api/run-experiment":
                self._send_json(run_workspace_experiment(self.workspace_root, payload["path"], payload.get("output_dir")))
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
    }


def _dashboard_summary(workspace: Path, dashboard_file: Path) -> dict[str, Any]:
    experiment_dir = dashboard_file.parent
    index = _read_json_file(experiment_dir / "index.json")
    manifest = _read_json_file(experiment_dir / "experiment_manifest.json")
    timeline_name = index.get("mode_timeline")
    timeline_data = _read_json_file(experiment_dir / str(timeline_name)) if timeline_name else {}
    experiment = manifest.get("experiment", {})
    scenario = experiment.get("scenario", {})
    runs = index.get("runs", []) if isinstance(index.get("runs"), list) else []
    best = _select_metric_row(runs, "final_error_deg", reverse=False)
    worst = _select_metric_row(runs, "final_error_deg", reverse=True)
    compare_run_ids = _compare_run_ids(runs, best, worst)
    files = []
    for name in [
        "README.md",
        "index.json",
        "summary_metrics.csv",
        "experiment_manifest.json",
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
        "files": files,
        "readme_url": _file_url(experiment_dir / "README.md", workspace) if (experiment_dir / "README.md").exists() else None,
        "dashboard_url": _file_url(dashboard_file, workspace),
    }


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
        path = candidate / "time_history.csv"
        if path.exists():
            return path
    return candidates[0] / "time_history.csv"


def _read_time_history(path: Path) -> list[dict[str, Any]]:
    columns = {
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


def _render_home() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>satmodel 仿真平台</title>
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
    .grid {
      display: grid;
      grid-template-columns: minmax(0, 1.12fr) minmax(380px, 0.88fr);
      gap: 16px;
      align-items: start;
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
    .full { grid-column: 1 / -1; }
    .path, .subtle {
      color: var(--muted);
      font-size: 13px;
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
      grid-template-columns: repeat(4, minmax(0, 1fr));
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
    }
    .timeline-cursor {
      position: absolute;
      top: -3px;
      width: 2px;
      bottom: -3px;
      background: #17202a;
      box-shadow: 0 0 0 2px rgba(255,255,255,0.5);
    }
    a {
      color: var(--accent-strong);
      text-decoration: none;
    }
    a:hover { text-decoration: underline; }
    @media (max-width: 1080px) {
      .hero, .grid, .cards, .form-grid, .summary-grid, .detail-grid, .compare-toolbar, .compare-grid {
        grid-template-columns: 1fr;
      }
      header { padding: 16px 14px 8px; }
      main { padding: 12px 14px 30px; }
      iframe { height: 520px; }
    }
  </style>
</head>
<body>
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
    <div class="grid">
      <div>
        <section>
          <h2>场景</h2>
          <div id="scenarios"></div>
        </section>
        <section>
          <h2>实验计划</h2>
          <div class="toolbar">
            <button id="refresh" class="secondary">刷新工作区</button>
            <input id="output" placeholder="可选输出目录，例如 results/platform_ui/demo">
          </div>
          <div id="experiments"></div>
        </section>
        <section>
          <h2>结果浏览</h2>
          <div id="dashboards"></div>
        </section>
      </div>
      <div>
        <section>
          <h2>创建实验</h2>
          <div class="form-grid">
            <label class="full">场景<select id="builder-scenario"></select></label>
            <label>实验名称<input id="builder-name" placeholder="quick_pd_sweep"></label>
            <label>输出目录<input id="builder-output" placeholder="results/platform_ui/quick_pd_sweep"></label>
            <label>扫描参数路径<input id="builder-sweep-path" value="controller.pd_kp"></label>
            <label>扫描取值<input id="builder-sweep-values" placeholder="1.2,1.5"></label>
            <label>Monte Carlo 样本数<input id="builder-mc-samples" type="number" min="0" value="0"></label>
            <label>随机种子<input id="builder-mc-seed" type="number" placeholder="10"></label>
            <label>任务模板<select id="builder-mission"><option value="single_mode">单模式保持</option><option value="detumble_then_hold">消旋后保持</option></select></label>
            <label>任务模式<input id="builder-mode" value="inertial_hold"></label>
            <label>消旋时长 s<input id="builder-detumble" type="number" step="0.1" value="0.5"></label>
            <label>参考目标<input id="builder-reference" value="body_zero"></label>
          </div>
          <div class="toolbar" style="margin-top:10px">
            <button class="primary" id="create-plan">创建实验计划</button>
          </div>
        </section>
        <section>
          <h2>工作区概览</h2>
          <div class="cards">
            <div class="card"><span>场景</span><strong id="scenario-count">0</strong></div>
            <div class="card"><span>实验计划</span><strong id="experiment-count">0</strong></div>
            <div class="card"><span>结果界面</span><strong id="dashboard-count">0</strong></div>
          </div>
        </section>
        <section>
          <h2>当前场景</h2>
          <div id="scenario-summary" class="empty">选择一个场景后，这里会显示时间、控制器、环境和输出配置。</div>
        </section>
        <section>
          <h2>当前结果</h2>
          <div id="result-summary" class="empty">运行实验或选择一个结果目录后，这里会显示实验摘要、最佳 run 和关键文件。</div>
        </section>
        <section>
          <h2>关键 Run 对比</h2>
          <div id="compare-view" class="empty">选择结果目录后，这里会显示最佳/最差或关键 run 的对照指标和曲线。</div>
        </section>
        <section>
          <h2>三维姿态回放</h2>
          <div id="replay-view" class="empty">选择结果目录后，这里会显示基于真实四元数的姿态回放。</div>
        </section>
        <section>
          <h2>结果预览</h2>
          <div id="preview-shell" class="preview-shell">
            <div class="preview-meta">
              <span class="subtle" id="preview-title">还没有加载结果界面</span>
              <div class="toolbar" style="margin:0">
                <button id="open-dashboard" class="secondary" type="button" disabled>新窗口打开</button>
              </div>
            </div>
            <div id="preview-empty" class="empty" style="margin:14px">选择结果界面后，会在这里直接预览带动画和仿真结果图的 dashboard。</div>
            <iframe id="preview-frame" title="dashboard preview" hidden></iframe>
          </div>
        </section>
        <section>
          <h2>状态</h2>
          <div class="status" id="status">就绪。</div>
        </section>
      </div>
    </div>
  </main>
  <script>
    const state = {
      workspace: null,
      currentDashboard: null,
      currentDashboardUrl: null,
      currentScenario: null,
    };
    const status = document.getElementById('status');
    const output = document.getElementById('output');
    const previewFrame = document.getElementById('preview-frame');
    const previewEmpty = document.getElementById('preview-empty');
    const previewTitle = document.getElementById('preview-title');
    const openDashboard = document.getElementById('open-dashboard');
    const compareView = document.getElementById('compare-view');
    const replayView = document.getElementById('replay-view');
    const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    const fmt = value => value === null || value === undefined || value === '' ? '—' : Number.isFinite(Number(value)) ? Number(value).toPrecision(5).replace(/\\.0+$/, '') : String(value);

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

    async function load({preserveSelection = true} = {}) {
      setStatus('正在读取工作区...');
      state.workspace = await api('/api/workspace');
      document.getElementById('workspace').textContent = state.workspace.workspace;
      document.getElementById('scenario-count').textContent = state.workspace.scenarios.length;
      document.getElementById('experiment-count').textContent = state.workspace.experiments.length;
      document.getElementById('dashboard-count').textContent = state.workspace.dashboards.length;
      renderExperiments();
      renderScenarios();
      renderDashboards();
      renderBuilder();
      if (state.workspace.scenarios.length && (!preserveSelection || !state.currentScenario)) {
        await showScenario(state.workspace.scenarios[0].path, true);
      }
      if (state.workspace.dashboards.length) {
        const preferred = preserveSelection
          ? state.workspace.dashboards.find(item => item.path === state.currentDashboard)?.path
          : null;
        await showDashboard(preferred || state.workspace.dashboards[state.workspace.dashboards.length - 1].path, true);
      } else if (!preserveSelection) {
        clearDashboardPreview();
      }
      setStatus('就绪。', 'ok');
    }

    function renderBuilder() {
      const select = document.getElementById('builder-scenario');
      select.innerHTML = state.workspace.scenarios.map(item => `<option value="${esc(item.path)}">${esc(item.name)} - ${esc(item.path)}</option>`).join('');
      if (!select.value && state.workspace.scenarios[0]) select.value = state.workspace.scenarios[0].path;
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
      const rows = state.workspace.experiments;
      if (!rows.length) {
        document.getElementById('experiments').innerHTML = '<div class="empty">scenarios/ 目录下还没有实验计划。</div>';
        return;
      }
      document.getElementById('experiments').innerHTML = `<table><thead><tr><th>名称</th><th>路径</th><th>运行规模</th><th>操作</th></tr></thead><tbody>${rows.map(plan => {
        const runs = plan.error ? plan.error : `${(plan.sweeps || 0) ? '参数扫描' : '单场景'} / MC ${plan.monte_carlo_samples || 0}`;
        return `<tr><td title="${esc(plan.name)}">${esc(plan.name)}</td><td title="${esc(plan.path)}">${esc(plan.path)}</td><td title="${esc(runs)}">${esc(runs)}</td><td><button onclick="validatePlan('${esc(plan.path)}')">校验</button> <button class="primary" onclick="runPlan('${esc(plan.path)}')">运行</button></td></tr>`;
      }).join('')}</tbody></table>`;
    }

    function renderDashboards() {
      const rows = state.workspace.dashboards;
      if (!rows.length) {
        document.getElementById('dashboards').innerHTML = '<div class="empty">还没有结果界面。运行一次实验后会自动生成。</div>';
        return;
      }
      document.getElementById('dashboards').innerHTML = `<table><thead><tr><th>名称</th><th>场景</th><th>Run</th><th>通过率</th><th>操作</th></tr></thead><tbody>${rows.map(item => {
        const rate = `${Math.round((item.acceptance_rate || 0) * 1000) / 10}%`;
        return `<tr><td title="${esc(item.path)}">${esc(item.name)}</td><td>${esc(item.scenario || '—')}</td><td>${esc(item.run_count)}</td><td>${esc(rate)}</td><td><button onclick="showDashboard('${esc(item.path)}')">预览</button> <button class="secondary" onclick="window.open('${esc(item.url)}', '_blank')">打开</button></td></tr>`;
      }).join('')}</tbody></table>`;
    }

    function renderScenarioSummary(result, validated = false) {
      state.currentScenario = result.path;
      document.getElementById('scenario-summary').innerHTML = `
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
    }

    function renderDashboardSummary(result) {
      state.currentDashboard = result.path;
      state.currentDashboardUrl = result.url;
      const best = result.best_run || {};
      const worst = result.worst_run || {};
      document.getElementById('result-summary').innerHTML = `
        <div class="summary-grid">
          <div class="summary-card"><span>实验名称</span><strong>${esc(result.experiment_name || result.name)}</strong></div>
          <div class="summary-card"><span>所属场景</span><strong>${esc(result.scenario_name || '—')}</strong></div>
          <div class="summary-card"><span>Run 数量</span><strong>${fmt(result.run_count)}</strong></div>
          <div class="summary-card"><span>通过率</span><strong>${Math.round((result.acceptance_rate || 0) * 1000) / 10}%</strong></div>
          <div class="summary-card"><span>最佳 Run</span><strong>${esc(result.best_run_id || '—')}</strong></div>
          <div class="summary-card"><span>最佳末端误差</span><strong>${fmt(result.best_final_error_deg)} deg</strong></div>
        </div>
        <div class="detail-grid">
          <div class="detail-box"><strong>最佳 Run 指标</strong><div>末端误差 ${fmt(best.final_error_deg)} deg / RMS ${fmt(best.rms_error_deg)} deg</div></div>
          <div class="detail-box"><strong>最差 Run 指标</strong><div>${esc(result.worst_run_id || '—')} / 末端误差 ${fmt(result.worst_final_error_deg)} deg</div></div>
        </div>
        <div class="detail-box"><strong>参数列</strong><div class="chips">${(result.parameter_columns || []).map(name => `<span class="chip">${esc(name)}</span>`).join('') || '<span class="subtle">暂无</span>'}</div></div>
        <div class="detail-box" style="margin-top:10px"><strong>指标列</strong><div class="chips">${(result.metric_columns || []).map(name => `<span class="chip">${esc(name)}</span>`).join('') || '<span class="subtle">暂无</span>'}</div></div>
        <div class="mini-table">
          <table>
            <thead><tr><th>Run</th><th>Accepted</th><th>Final error deg</th><th>RMS error deg</th></tr></thead>
            <tbody>
              ${result.runs.slice(0, 4).map(row => `<tr><td>${esc(row.run_id)}</td><td>${esc(row.accepted)}</td><td>${fmt(row.final_error_deg)}</td><td>${fmt(row.rms_error_deg)}</td></tr>`).join('')}
            </tbody>
          </table>
        </div>
        <div class="files">${(result.files || []).map(file => `<a href="${file.url}" target="_blank">${esc(file.name)}</a>`).join('')}</div>
      `;
      previewTitle.textContent = `${result.experiment_name || result.name} / ${result.scenario_name || '未命名场景'}`;
      previewFrame.hidden = false;
      previewEmpty.hidden = true;
      previewFrame.src = result.url;
      openDashboard.disabled = false;
      renderCompareView(result);
      renderReplayView(result);
    }

    function clearDashboardPreview() {
      state.currentDashboard = null;
      state.currentDashboardUrl = null;
      document.getElementById('result-summary').innerHTML = '运行实验或选择一个结果目录后，这里会显示实验摘要、最佳 run 和关键文件。';
      compareView.innerHTML = '选择结果目录后，这里会显示最佳/最差或关键 run 的对照指标和曲线。';
      replayView.innerHTML = '选择结果目录后，这里会显示基于真实四元数的姿态回放。';
      stopReplayAnimation();
      previewTitle.textContent = '还没有加载结果界面';
      previewFrame.hidden = true;
      previewFrame.removeAttribute('src');
      previewEmpty.hidden = false;
      openDashboard.disabled = true;
    }

    function renderCompareView(result) {
      const runRows = new Map((result.runs || []).map(row => [row.run_id, row]));
      const compareIds = (result.compare_run_ids || []).filter(runId => runRows.has(runId));
      const histories = result.compare_histories || {};
      if (!compareIds.length) {
        compareView.innerHTML = '<div class="empty">当前结果没有可用于对比的关键 run。</div>';
        return;
      }
      const defaultA = compareIds[0];
      const defaultB = compareIds[1] || compareIds[0];
      compareView.innerHTML = `
        <div class="compare-toolbar">
          <label>对比 Run A<select id="compare-a">${compareIds.map(runId => `<option value="${esc(runId)}">${esc(runId)}</option>`).join('')}</select></label>
          <label>对比 Run B<select id="compare-b">${compareIds.map(runId => `<option value="${esc(runId)}">${esc(runId)}</option>`).join('')}</select></label>
        </div>
        <div class="compare-grid">
          <div id="compare-card-a" class="compare-card"></div>
          <div id="compare-card-b" class="compare-card"></div>
        </div>
        <svg id="compare-attitude" class="compare-chart" role="img" aria-label="姿态误差对比图"></svg>
        <svg id="compare-torque" class="compare-chart" role="img" aria-label="控制力矩对比图"></svg>
      `;
      const selectA = document.getElementById('compare-a');
      const selectB = document.getElementById('compare-b');
      selectA.value = defaultA;
      selectB.value = defaultB;

      const update = () => {
        const runA = runRows.get(selectA.value) || {};
        const runB = runRows.get(selectB.value) || {};
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
      };
      selectA.addEventListener('change', update);
      selectB.addEventListener('change', update);
      update();
    }

    function renderCompareCard(row, slot) {
      return `
        <h3>Run ${slot} · ${esc(row.run_id || '—')}</h3>
        <div class="compare-metrics">
          <div><span>末端误差</span><strong>${fmt(row.final_error_deg)} deg</strong></div>
          <div><span>RMS 误差</span><strong>${fmt(row.rms_error_deg)} deg</strong></div>
          <div><span>峰值力矩</span><strong>${fmt(row.peak_torque_nm)} N m</strong></div>
          <div><span>验收状态</span><strong>${esc(row.accepted)}</strong></div>
        </div>
      `;
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
        replayView.innerHTML = '<div class="empty">当前结果还没有姿态四元数历史。重新运行后即可启用三维回放。</div>';
        stopReplayAnimation();
        return;
      }
      const timeline = Array.isArray(result.timeline?.timeline) ? result.timeline.timeline : [];
      const duration = Number(result.timeline?.duration_s || 0);
      replayView.innerHTML = `
        <div class="replay-toolbar">
          <label>回放 Run<select id="replay-run">${runIds.map(runId => `<option value="${esc(runId)}">${esc(runId)}</option>`).join('')}</select></label>
          <button id="replay-play" class="secondary" type="button">播放</button>
          <button id="replay-reset" class="secondary" type="button">回到起点</button>
        </div>
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
          </div>
        </div>
        <input id="replay-slider" class="replay-slider" type="range" min="0" max="0" step="1" value="0">
        <div class="timeline-strip" id="replay-timeline">
          <div class="timeline-legend">
            <span>任务模式时间线</span>
            <span id="replay-mode-window">${duration > 0 ? `0-${fmt(duration)} s` : '暂无 timeline'}</span>
          </div>
          <div class="timeline-bar" id="timeline-bar"></div>
        </div>
      `;
      const select = document.getElementById('replay-run');
      const slider = document.getElementById('replay-slider');
      const playButton = document.getElementById('replay-play');
      const resetButton = document.getElementById('replay-reset');
      const replayState = {
        histories,
        runIds,
        currentRunId: runIds[0],
        frameIndex: 0,
        timeline,
        duration,
      };
      ensureReplayScene();
      renderReplayTimeline(timeline, duration);

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
        applyReplaySample(sample);
        updateReplayCursor(Number(sample.time_s), replayState.duration);
      };

      const updateRun = (runId) => {
        replayState.currentRunId = runId;
        const history = replayState.histories[runId] || [];
        slider.max = String(Math.max(history.length - 1, 0));
        updateFrame(0);
      };

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
      updateRun(runIds[0]);
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
        return `<div class="timeline-segment" style="left:${left}%;width:${width}%;background:${colors[index % colors.length]}" title="${esc(item.mode || item.name || 'mode')} ${fmt(start)}-${fmt(stop)} s"></div>`;
      }).join('');
      bar.innerHTML = `${segments}<div id="timeline-cursor" class="timeline-cursor" style="left:0%"></div>`;
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

    async function validatePlan(path) {
      try {
        setStatus(`正在校验 ${path}...`);
        const result = await api('/api/validate-experiment', {method:'POST', body: JSON.stringify({path})});
        setStatus(`实验计划有效：${result.name}，run=${result.runs}，场景=${result.scenario}`, 'ok');
      } catch (err) {
        setStatus(err.message, 'bad');
      }
    }

    async function showScenario(path, quiet = false) {
      try {
        if (!quiet) setStatus(`正在读取 ${path}...`);
        const result = await api('/api/scenario', {method:'POST', body: JSON.stringify({path})});
        renderScenarioSummary(result);
        document.getElementById('builder-scenario').value = path;
        if (!document.getElementById('builder-name').value) {
          document.getElementById('builder-name').value = `${result.name}_experiment`;
        }
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
        setStatus(`场景有效：${result.name}，${result.duration_s}s，dt=${result.dt_s}，${result.system}/${result.controller}`, 'ok');
      } catch (err) {
        setStatus(err.message, 'bad');
      }
    }

    async function showDashboard(path, quiet = false) {
      try {
        if (!quiet) setStatus(`正在加载 ${path}...`);
        const result = await api('/api/dashboard', {method:'POST', body: JSON.stringify({path})});
        renderDashboardSummary(result);
        if (!quiet) setStatus(`已加载结果：${result.experiment_name}，run=${result.run_count}，最佳=${result.best_run_id || '—'}`, 'ok');
      } catch (err) {
        setStatus(err.message, 'bad');
      }
    }

    async function runPlan(path) {
      try {
        setStatus(`正在运行 ${path}...`);
        const body = {path};
        if (output.value.trim()) body.output_dir = output.value.trim();
        const result = await api('/api/run-experiment', {method:'POST', body: JSON.stringify(body)});
        await load({preserveSelection: false});
        if (result.summary) {
          renderDashboardSummary(result.summary);
        } else {
          await showDashboard(result.dashboard, true);
        }
        setStatus(`完成：${result.runs} 个 run，通过=${result.accepted}，失败=${result.failed}。`, 'ok');
      } catch (err) {
        setStatus(err.message, 'bad');
      }
    }

    async function createPlan() {
      try {
        const scenario = document.getElementById('builder-scenario').value;
        if (!scenario) throw new Error('还没有选择场景。');
        const body = {
          scenario_path: scenario,
          name: document.getElementById('builder-name').value || 'generated_experiment',
          output_root: document.getElementById('builder-output').value,
          sweep_path: document.getElementById('builder-sweep-path').value,
          sweep_values: document.getElementById('builder-sweep-values').value,
          monte_carlo_samples: Number(document.getElementById('builder-mc-samples').value || 0),
          monte_carlo_seed: document.getElementById('builder-mc-seed').value,
          mission_template: document.getElementById('builder-mission').value,
          mode: document.getElementById('builder-mode').value,
          hold_mode: document.getElementById('builder-mode').value,
          detumble_s: Number(document.getElementById('builder-detumble').value || 0.5),
          reference: document.getElementById('builder-reference').value,
        };
        setStatus(`正在创建 ${body.name}...`);
        const result = await api('/api/create-experiment', {method:'POST', body: JSON.stringify(body)});
        await load();
        setStatus(`已创建 ${result.path}；校验 run=${result.validation.runs}。`, 'ok');
      } catch (err) {
        setStatus(err.message, 'bad');
      }
    }

    openDashboard.addEventListener('click', () => {
      if (state.currentDashboardUrl) window.open(state.currentDashboardUrl, '_blank');
    });
    document.getElementById('refresh').addEventListener('click', () => load());
    document.getElementById('create-plan').addEventListener('click', createPlan);
    load().catch(err => setStatus(err.message, 'bad'));
  </script>
</body>
</html>
"""
