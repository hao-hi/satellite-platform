"""Local browser UI for operating satmodel platform experiments."""

from __future__ import annotations

import json
import mimetypes
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from satmodel._version import __version__
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
                    }
                )
        except Exception as exc:
            experiments.append({"name": path.stem, "path": rel, "error": str(exc)})
    dashboards = []
    for path in sorted(results_dir.rglob("dashboard.html")) if results_dir.exists() else []:
        dashboards.append(
            {
                "name": path.parent.name,
                "path": _relative(path, workspace),
                "url": _file_url(path, workspace),
            }
        )
    return {
        "workspace": str(workspace),
        "satmodel_version": __version__,
        "scenarios": scenarios,
        "experiments": experiments,
        "dashboards": dashboards,
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
            if parsed.path == "/api/validate-experiment":
                self._send_json(validate_workspace_experiment(self.workspace_root, payload["path"]))
                return
            if parsed.path == "/api/run-experiment":
                self._send_json(run_workspace_experiment(self.workspace_root, payload["path"], payload.get("output_dir")))
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


def _render_home() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>satmodel platform</title>
  <style>
    :root {
      --bg: #f5f7fa;
      --panel: #ffffff;
      --ink: #17202a;
      --muted: #647083;
      --line: #d8dee8;
      --accent: #2364aa;
      --ok: #247a48;
      --bad: #b42318;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }
    header {
      background: var(--panel);
      border-bottom: 1px solid var(--line);
      padding: 18px 24px;
    }
    h1 { margin: 0; font-size: 22px; }
    main {
      max-width: 1300px;
      margin: 0 auto;
      padding: 18px 24px 36px;
    }
    .grid {
      display: grid;
      grid-template-columns: minmax(0, 1.15fr) minmax(340px, .75fr);
      gap: 14px;
      align-items: start;
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      margin-bottom: 14px;
    }
    h2 { margin: 0 0 12px; font-size: 16px; }
    table { width: 100%; border-collapse: collapse; table-layout: fixed; }
    th, td {
      border-bottom: 1px solid #e8ecf2;
      padding: 8px 7px;
      text-align: left;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    th { color: var(--muted); background: #fafbfc; font-weight: 650; }
    button, input {
      min-height: 34px;
      border-radius: 6px;
      border: 1px solid var(--line);
      padding: 7px 10px;
      background: #fff;
      color: var(--ink);
    }
    button {
      cursor: pointer;
      border-color: #b9c4d5;
      font-weight: 650;
    }
    button.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: white;
    }
    button:disabled { opacity: .55; cursor: wait; }
    .toolbar { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 10px; }
    .path { color: var(--muted); font-size: 13px; }
    .status { color: var(--muted); min-height: 22px; }
    .ok { color: var(--ok); }
    .bad { color: var(--bad); }
    .cards { display: grid; grid-template-columns: repeat(3, minmax(120px, 1fr)); gap: 10px; }
    .card { border: 1px solid var(--line); border-radius: 8px; padding: 12px; background: #fbfcfe; }
    .card span { display: block; color: var(--muted); font-size: 12px; }
    .card strong { display: block; font-size: 21px; margin-top: 4px; }
    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }
    @media (max-width: 900px) {
      main { padding: 14px; }
      .grid, .cards { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>satmodel Platform</h1>
    <div class="path" id="workspace"></div>
  </header>
  <main>
    <div class="grid">
      <div>
        <section>
          <h2>Experiment Plans</h2>
          <div class="toolbar">
            <button id="refresh">Refresh</button>
            <input id="output" placeholder="Optional output dir, e.g. results/platform_ui/demo">
          </div>
          <div id="experiments"></div>
        </section>
        <section>
          <h2>Dashboards</h2>
          <div id="dashboards"></div>
        </section>
      </div>
      <div>
        <section>
          <h2>Workspace</h2>
          <div class="cards">
            <div class="card"><span>Scenarios</span><strong id="scenario-count">0</strong></div>
            <div class="card"><span>Experiments</span><strong id="experiment-count">0</strong></div>
            <div class="card"><span>Dashboards</span><strong id="dashboard-count">0</strong></div>
          </div>
        </section>
        <section>
          <h2>Status</h2>
          <div class="status" id="status">Ready.</div>
        </section>
      </div>
    </div>
  </main>
  <script>
    const state = { workspace: null };
    const status = document.getElementById('status');
    const output = document.getElementById('output');
    const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
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
    async function load() {
      setStatus('Loading workspace...');
      state.workspace = await api('/api/workspace');
      document.getElementById('workspace').textContent = state.workspace.workspace;
      document.getElementById('scenario-count').textContent = state.workspace.scenarios.length;
      document.getElementById('experiment-count').textContent = state.workspace.experiments.length;
      document.getElementById('dashboard-count').textContent = state.workspace.dashboards.length;
      renderExperiments();
      renderDashboards();
      setStatus('Ready.', 'ok');
    }
    function renderExperiments() {
      const rows = state.workspace.experiments;
      if (!rows.length) {
        document.getElementById('experiments').innerHTML = '<div class="path">No experiment plans found in scenarios/.</div>';
        return;
      }
      document.getElementById('experiments').innerHTML = `<table><thead><tr><th>Name</th><th>Path</th><th>Runs</th><th>Actions</th></tr></thead><tbody>${rows.map(plan => {
        const runs = plan.error ? plan.error : `${(plan.sweeps || 0) ? 'sweep' : 'single'} / MC ${plan.monte_carlo_samples || 0}`;
        return `<tr><td title="${esc(plan.name)}">${esc(plan.name)}</td><td title="${esc(plan.path)}">${esc(plan.path)}</td><td title="${esc(runs)}">${esc(runs)}</td><td><button onclick="validatePlan('${esc(plan.path)}')">Validate</button> <button class="primary" onclick="runPlan('${esc(plan.path)}')">Run</button></td></tr>`;
      }).join('')}</tbody></table>`;
    }
    function renderDashboards() {
      const rows = state.workspace.dashboards;
      if (!rows.length) {
        document.getElementById('dashboards').innerHTML = '<div class="path">No dashboards yet. Run an experiment to create one.</div>';
        return;
      }
      document.getElementById('dashboards').innerHTML = `<table><thead><tr><th>Name</th><th>Path</th></tr></thead><tbody>${rows.map(item => `<tr><td>${esc(item.name)}</td><td><a href="${item.url}" target="_blank">${esc(item.path)}</a></td></tr>`).join('')}</tbody></table>`;
    }
    async function validatePlan(path) {
      try {
        setStatus(`Validating ${path}...`);
        const result = await api('/api/validate-experiment', {method:'POST', body: JSON.stringify({path})});
        setStatus(`Valid: ${result.name}, runs=${result.runs}, scenario=${result.scenario}`, 'ok');
      } catch (err) {
        setStatus(err.message, 'bad');
      }
    }
    async function runPlan(path) {
      try {
        setStatus(`Running ${path}...`);
        const body = {path};
        if (output.value.trim()) body.output_dir = output.value.trim();
        const result = await api('/api/run-experiment', {method:'POST', body: JSON.stringify(body)});
        setStatus(`Done: ${result.runs} runs, accepted=${result.accepted}, failed=${result.failed}.`, 'ok');
        await load();
        window.open(result.dashboard_url, '_blank');
      } catch (err) {
        setStatus(err.message, 'bad');
      }
    }
    document.getElementById('refresh').addEventListener('click', load);
    load().catch(err => setStatus(err.message, 'bad'));
  </script>
</body>
</html>
"""
