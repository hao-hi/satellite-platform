"""Local browser UI for operating satmodel platform experiments."""

from __future__ import annotations

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
    experiment = manifest.get("experiment", {})
    scenario = experiment.get("scenario", {})
    runs = index.get("runs", []) if isinstance(index.get("runs"), list) else []
    best = _select_metric_row(runs, "final_error_deg", reverse=False)
    worst = _select_metric_row(runs, "final_error_deg", reverse=True)
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
    a {
      color: var(--accent-strong);
      text-decoration: none;
    }
    a:hover { text-decoration: underline; }
    @media (max-width: 1080px) {
      .hero, .grid, .cards, .form-grid, .summary-grid, .detail-grid {
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
    }

    function clearDashboardPreview() {
      state.currentDashboard = null;
      state.currentDashboardUrl = null;
      document.getElementById('result-summary').innerHTML = '运行实验或选择一个结果目录后，这里会显示实验摘要、最佳 run 和关键文件。';
      previewTitle.textContent = '还没有加载结果界面';
      previewFrame.hidden = true;
      previewFrame.removeAttribute('src');
      previewEmpty.hidden = false;
      openDashboard.disabled = true;
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
