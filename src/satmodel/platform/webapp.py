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


def _render_home() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>satmodel 仿真平台</title>
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
      font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
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
    button, input, select {
      min-height: 34px;
      border-radius: 6px;
      border: 1px solid var(--line);
      padding: 7px 10px;
      background: #fff;
      color: var(--ink);
    }
    label { display: grid; gap: 5px; color: var(--muted); font-size: 12px; }
    label input, label select { color: var(--ink); font-size: 14px; }
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
    .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .full { grid-column: 1 / -1; }
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
    <h1>satmodel 卫星姿态控制仿真平台</h1>
    <div class="path" id="workspace"></div>
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
            <button id="refresh">刷新</button>
            <input id="output" placeholder="可选输出目录，例如 results/platform_ui/demo">
          </div>
          <div id="experiments"></div>
        </section>
        <section>
          <h2>结果界面</h2>
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
          <h2>工作区</h2>
          <div class="cards">
            <div class="card"><span>场景</span><strong id="scenario-count">0</strong></div>
            <div class="card"><span>实验计划</span><strong id="experiment-count">0</strong></div>
            <div class="card"><span>结果界面</span><strong id="dashboard-count">0</strong></div>
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
        document.getElementById('scenarios').innerHTML = '<div class="path">scenarios/ 目录下还没有普通场景文件。</div>';
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
        document.getElementById('experiments').innerHTML = '<div class="path">scenarios/ 目录下还没有实验计划。</div>';
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
        document.getElementById('dashboards').innerHTML = '<div class="path">还没有结果界面。运行一次实验后会自动生成。</div>';
        return;
      }
      document.getElementById('dashboards').innerHTML = `<table><thead><tr><th>名称</th><th>路径</th></tr></thead><tbody>${rows.map(item => `<tr><td>${esc(item.name)}</td><td><a href="${item.url}" target="_blank">${esc(item.path)}</a></td></tr>`).join('')}</tbody></table>`;
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
    async function showScenario(path) {
      try {
        setStatus(`正在读取 ${path}...`);
        const result = await api('/api/scenario', {method:'POST', body: JSON.stringify({path})});
        setStatus(`${result.name}：${result.duration_s}s，dt=${result.dt_s}，${result.system}/${result.controller}/${result.environment}，故障=${result.fault_count}`, 'ok');
        document.getElementById('builder-scenario').value = path;
        if (!document.getElementById('builder-name').value) {
          document.getElementById('builder-name').value = `${result.name}_experiment`;
        }
      } catch (err) {
        setStatus(err.message, 'bad');
      }
    }
    async function validateScenario(path) {
      try {
        setStatus(`正在校验 ${path}...`);
        const result = await api('/api/validate-scenario', {method:'POST', body: JSON.stringify({path})});
        setStatus(`场景有效：${result.name}，${result.duration_s}s，dt=${result.dt_s}，${result.system}/${result.controller}`, 'ok');
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
        setStatus(`完成：${result.runs} 个 run，通过=${result.accepted}，失败=${result.failed}。`, 'ok');
        await load();
        window.open(result.dashboard_url, '_blank');
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
        setStatus(`已创建 ${result.path}；校验 run=${result.validation.runs}。`, 'ok');
        await load();
      } catch (err) {
        setStatus(err.message, 'bad');
      }
    }
    document.getElementById('refresh').addEventListener('click', load);
    document.getElementById('create-plan').addEventListener('click', createPlan);
    load().catch(err => setStatus(err.message, 'bad'));
  </script>
</body>
</html>
"""
