"""Static HTML dashboard generation for experiment result directories."""

from __future__ import annotations

import csv
import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from satmodel._version import __version__


def build_dashboard(experiment_dir: str | Path, filename: str = "dashboard.html") -> Path:
    """Build a self-contained dashboard for an experiment output directory."""

    root = Path(experiment_dir)
    index = _read_json(root / "index.json", default={})
    manifest = _read_json(root / "experiment_manifest.json", default={})
    rows = _read_csv(root / "summary_metrics.csv")
    rows = [_with_dashboard_links(row, root) for row in rows]
    runtime_name = index.get("runtime_schedule")
    timeline_name = index.get("mode_timeline")
    runtime = _read_json(root / runtime_name, default=None) if runtime_name else None
    timeline = _read_json(root / timeline_name, default=None) if timeline_name else None
    payload = {
        "satmodel_version": __version__,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "index": index,
        "manifest": manifest,
        "rows": rows,
        "runtime": runtime,
        "timeline": timeline,
        "time_history": _time_history_for_rows(root, rows),
    }
    path = root / filename
    path.write_text(_render_dashboard(payload), encoding="utf-8")
    return path


def _read_json(path: Path, *, default):
    if path is None or not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _with_dashboard_links(row: dict[str, str], root: Path) -> dict[str, str]:
    payload = dict(row)
    output_dir = payload.get("output_dir")
    if output_dir:
        try:
            relative = Path(output_dir).resolve().relative_to(root.resolve())
            payload["dashboard_output_href"] = (relative / "README.md").as_posix()
        except ValueError:
            payload["dashboard_output_href"] = str(Path(output_dir) / "README.md")
    return payload


def _time_history_for_rows(root: Path, rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    histories: dict[str, list[dict[str, str]]] = {}
    if not rows:
        return histories
    for row in rows:
        run_id = row.get("run_id") or f"run_{len(histories):03d}"
        path = _time_history_path(root, row)
        history = _read_csv(path)
        if history:
            histories[run_id] = _sample_history(history)
    return histories


def _time_history_path(root: Path, row: dict[str, str]) -> Path:
    output_dir = row.get("output_dir")
    if not output_dir:
        return root / "time_history.csv"
    run_dir = Path(output_dir)
    candidates = [run_dir, root / run_dir, root / run_dir.name]
    if row.get("run_id"):
        candidates.append(root / row["run_id"])
    for candidate in candidates:
        path = candidate / "time_history.csv"
        if path.exists():
            return path
    return candidates[0] / "time_history.csv"


def _sample_history(rows: list[dict[str, str]], max_points: int = 1200) -> list[dict[str, str]]:
    if len(rows) <= max_points:
        return rows
    step = max(1, len(rows) // max_points)
    sampled = rows[::step]
    if sampled[-1] != rows[-1]:
        sampled.append(rows[-1])
    return sampled


def _render_dashboard(payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, default=str).replace("</", "<\\/")
    experiment = payload.get("manifest", {}).get("experiment", {})
    title = experiment.get("metadata", {}).get("name") or "satmodel experiment"
    template = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__TITLE__ 结果界面</title>
  <style>
    :root {
      --bg: #f6f7f9;
      --panel: #ffffff;
      --ink: #17202a;
      --muted: #637083;
      --line: #d9dee7;
      --accent: #2364aa;
      --ok: #247a48;
      --bad: #b42318;
      --warn: #a45f00;
      --teal: #087f8c;
      --violet: #6f4aa8;
      --amber: #b56b00;
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
      background: #ffffff;
      border-bottom: 1px solid var(--line);
      padding: 18px 24px 14px;
      position: sticky;
      top: 0;
      z-index: 4;
    }
    .topline {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      flex-wrap: wrap;
    }
    h1 {
      margin: 0;
      font-size: 22px;
      line-height: 1.2;
      font-weight: 720;
    }
    .meta {
      color: var(--muted);
      display: flex;
      gap: 14px;
      flex-wrap: wrap;
      margin-top: 8px;
    }
    main {
      padding: 18px 24px 32px;
      max-width: 1540px;
      margin: 0 auto;
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(5, minmax(130px, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }
    .stat {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      min-height: 78px;
    }
    .stat span {
      color: var(--muted);
      display: block;
      font-size: 12px;
    }
    .stat strong {
      display: block;
      font-size: 24px;
      margin-top: 4px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .layout {
      display: grid;
      grid-template-columns: minmax(0, 1.45fr) minmax(340px, 0.8fr);
      gap: 14px;
      align-items: start;
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      margin-bottom: 14px;
      overflow: hidden;
    }
    h2 {
      margin: 0 0 12px;
      font-size: 15px;
      font-weight: 700;
    }
    .toolbar {
      display: grid;
      grid-template-columns: minmax(180px, 1fr) 180px 160px;
      gap: 10px;
      margin-bottom: 12px;
    }
    .vizbar {
      display: grid;
      grid-template-columns: minmax(180px, 1fr) auto auto;
      gap: 10px;
      align-items: center;
      margin-bottom: 12px;
    }
    input, select, button {
      width: 100%;
      min-height: 36px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 7px 9px;
      color: var(--ink);
      background: #fff;
    }
    button {
      cursor: pointer;
      border-color: #b9c4d5;
      font-weight: 650;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }
    th, td {
      border-bottom: 1px solid #e7ebf1;
      text-align: left;
      padding: 8px 7px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      font-size: 13px;
    }
    th {
      color: var(--muted);
      font-weight: 650;
      background: #fafbfc;
      position: sticky;
      top: 82px;
      z-index: 2;
    }
    .status {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-weight: 650;
    }
    .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      display: inline-block;
      background: var(--ok);
    }
    .status.fail .dot { background: var(--bad); }
    .chart, .line-chart {
      width: 100%;
      display: block;
      border: 1px solid #e7ebf1;
      border-radius: 6px;
      background: #fbfcfe;
    }
    .chart { height: 250px; }
    .line-chart { height: 220px; }
    .axis, .grid { stroke: #ccd3df; stroke-width: 1; }
    .grid { opacity: .7; }
    .bar { fill: var(--accent); }
    .bar.fail { fill: var(--bad); }
    .bar.best { fill: var(--teal); }
    .viz-grid {
      display: grid;
      grid-template-columns: minmax(320px, .8fr) minmax(0, 1.2fr);
      gap: 12px;
      align-items: stretch;
    }
    .animation-panel {
      border: 1px solid #e7ebf1;
      border-radius: 8px;
      background: #fbfcfe;
      padding: 12px;
      min-height: 332px;
      display: grid;
      gap: 10px;
      align-content: start;
    }
    .sat-stage {
      width: 100%;
      height: 220px;
      border-radius: 6px;
      border: 1px solid #e2e7ef;
      background: linear-gradient(#f9fbfd, #eef3f8);
    }
    .sat-body {
      transform-origin: 160px 105px;
      transition: transform 90ms linear;
    }
    .solar-left, .solar-right {
      transform-origin: center;
      animation: panelPulse 1.8s ease-in-out infinite;
    }
    @keyframes panelPulse {
      0%, 100% { opacity: .78; }
      50% { opacity: 1; }
    }
    .readout {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }
    .readout div {
      border: 1px solid #e7ebf1;
      border-radius: 6px;
      padding: 8px;
      background: #fff;
    }
    .readout span {
      display: block;
      color: var(--muted);
      font-size: 12px;
    }
    .plots {
      display: grid;
      gap: 10px;
    }
    .plot-title {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      color: var(--muted);
      font-size: 12px;
      margin: 0 0 4px;
    }
    .timeline {
      display: grid;
      gap: 8px;
    }
    .segment {
      display: grid;
      grid-template-columns: 110px minmax(0, 1fr) 90px;
      gap: 8px;
      align-items: center;
    }
    .track {
      height: 24px;
      background: #edf1f6;
      border-radius: 6px;
      overflow: hidden;
      position: relative;
    }
    .fill {
      height: 100%;
      min-width: 2px;
      position: absolute;
      background: var(--violet);
    }
    .runtime-list {
      max-height: 310px;
      overflow: auto;
      border: 1px solid #e7ebf1;
      border-radius: 6px;
    }
    .runtime-list table th { top: 0; }
    .details {
      display: grid;
      gap: 10px;
    }
    .details > div {
      border: 1px solid #e7ebf1;
      border-radius: 6px;
      padding: 10px;
      background: #fbfcfe;
    }
    .details strong {
      display: block;
      margin-bottom: 6px;
      font-size: 13px;
    }
    .insight-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }
    .tags {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .tag {
      display: inline-flex;
      align-items: center;
      min-height: 28px;
      padding: 4px 10px;
      border-radius: 999px;
      border: 1px solid #d4dbe7;
      background: #fff;
      color: var(--ink);
      font-size: 12px;
      font-weight: 650;
    }
    .empty {
      color: var(--muted);
      padding: 18px;
      border: 1px dashed var(--line);
      border-radius: 8px;
      background: #fbfcfe;
    }
    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }
    @media (max-width: 980px) {
      main { padding: 14px; }
      .stats, .layout, .toolbar, .vizbar, .viz-grid { grid-template-columns: 1fr; }
      .insight-grid { grid-template-columns: 1fr; }
      th { position: static; }
      .segment { grid-template-columns: 90px minmax(0, 1fr); }
      .segment .time { grid-column: 1 / -1; }
    }
  </style>
</head>
<body>
  <header>
    <div class="topline">
      <h1 id="title"></h1>
      <div class="meta">
        <span id="created"></span>
        <span id="version"></span>
      </div>
    </div>
  </header>
  <main>
    <div class="stats" id="stats"></div>
    <div class="layout">
      <div>
        <section>
          <h2>指标总览</h2>
          <div class="toolbar">
            <input id="search" type="search" placeholder="筛选 run、参数或验收状态">
            <select id="metric"></select>
            <select id="accepted">
              <option value="all">全部 run</option>
              <option value="accepted">仅通过</option>
              <option value="failed">仅失败</option>
            </select>
          </div>
          <svg class="chart" id="chart" role="img" aria-label="指标柱状图"></svg>
        </section>
        <section>
          <h2>仿真结果图</h2>
          <div class="vizbar">
            <select id="run-select"></select>
            <button id="play">播放动画</button>
            <button id="reset">回到起点</button>
          </div>
          <div id="visualization"></div>
        </section>
        <section>
          <h2>运行列表</h2>
          <div id="runs"></div>
        </section>
      </div>
      <div>
        <section>
          <h2>诊断摘要</h2>
          <div id="diagnostics"></div>
        </section>
        <section>
          <h2>任务模式时间线</h2>
          <div id="timeline"></div>
        </section>
        <section>
          <h2>运行时调度</h2>
          <div id="runtime"></div>
        </section>
        <section>
          <h2>文件索引</h2>
          <div id="files"></div>
        </section>
      </div>
    </div>
  </main>
  <script id="payload" type="application/json">__DATA__</script>
  <script>
    const data = JSON.parse(document.getElementById('payload').textContent);
    const index = data.index || {};
    const manifest = data.manifest || {};
    const experiment = manifest.experiment || {};
    const rows = data.rows || [];
    const histories = data.time_history || {};
    const numeric = value => value !== '' && value !== null && value !== undefined && !Number.isNaN(Number(value));
    const fmt = value => numeric(value) ? Number(value).toPrecision(5).replace(/\\.0+$/, '') : '';
    const metricColumns = (index.metric_columns || []).filter(name => rows.some(row => numeric(row[name])));
    let activeMetric = metricColumns.includes('final_error_deg') ? 'final_error_deg' : metricColumns[0];
    let activeRun = histories[index.best_run_id] ? index.best_run_id : Object.keys(histories)[0];
    let animationTimer = null;
    let animationIndex = 0;

    document.getElementById('title').textContent = experiment.metadata?.name || 'satmodel 实验';
    document.getElementById('created').textContent = `生成时间 ${(data.created_at_utc || '').slice(0, 19)}`;
    document.getElementById('version').textContent = `satmodel ${data.satmodel_version || ''}`;

    const statItems = [
      ['Run 数量', index.run_count ?? rows.length],
      ['通过', index.accepted_count ?? rows.filter(row => row.accepted === 'True').length],
      ['失败', index.failed_count ?? rows.filter(row => row.accepted === 'False').length],
      ['通过率', `${Math.round((index.acceptance_rate ?? 0) * 1000) / 10}%`],
      ['最佳 Run', index.best_run_id || ''],
    ];
    document.getElementById('stats').innerHTML = statItems.map(([label, value]) =>
      `<div class="stat"><span>${label}</span><strong title="${value}">${value}</strong></div>`
    ).join('');

    const metricSelect = document.getElementById('metric');
    metricSelect.innerHTML = metricColumns.map(name => `<option value="${name}">${name}</option>`).join('');
    if (activeMetric) metricSelect.value = activeMetric;

    const runSelect = document.getElementById('run-select');
    runSelect.innerHTML = Object.keys(histories).map(id => `<option value="${htmlEscape(id)}">${htmlEscape(id)}</option>`).join('');
    if (activeRun) runSelect.value = activeRun;

    function filteredRows() {
      const q = document.getElementById('search').value.trim().toLowerCase();
      const accepted = document.getElementById('accepted').value;
      return rows.filter(row => {
        const ok = String(row.accepted).toLowerCase() === 'true';
        if (accepted === 'accepted' && !ok) return false;
        if (accepted === 'failed' && ok) return false;
        if (!q) return true;
        return Object.values(row).some(value => String(value).toLowerCase().includes(q));
      });
    }

    function renderChart(items) {
      const svg = document.getElementById('chart');
      svg.innerHTML = '';
      if (!activeMetric || !items.length) {
        svg.innerHTML = '<text x="20" y="38" fill="#637083">暂无指标数据</text>';
        return;
      }
      const width = svg.clientWidth || 800;
      const height = svg.clientHeight || 250;
      const pad = {left: 44, right: 14, top: 18, bottom: 48};
      const values = items.map(row => Number(row[activeMetric])).filter(value => !Number.isNaN(value));
      const max = Math.max(...values, 1e-9);
      const min = Math.min(...values, 0);
      const span = Math.max(max - min, 1e-9);
      const plotW = width - pad.left - pad.right;
      const plotH = height - pad.top - pad.bottom;
      const barW = Math.max(4, plotW / Math.max(items.length, 1) * 0.72);
      const bestId = index.best_run_id;
      const grid = [0, .25, .5, .75, 1].map(t => {
        const y = pad.top + plotH * t;
        return `<line class="grid" x1="${pad.left}" y1="${y}" x2="${width - pad.right}" y2="${y}"/>`;
      }).join('');
      const bars = items.map((row, i) => {
        const value = Number(row[activeMetric]);
        const x = pad.left + (i + .14) * plotW / Math.max(items.length, 1);
        const h = Number.isNaN(value) ? 0 : Math.max(1, ((value - min) / span) * plotH);
        const y = pad.top + plotH - h;
        const cls = row.run_id === bestId ? 'bar best' : String(row.accepted).toLowerCase() === 'true' ? 'bar' : 'bar fail';
        return `<rect class="${cls}" x="${x}" y="${y}" width="${barW}" height="${h}"><title>${row.run_id} ${activeMetric}=${fmt(value)}</title></rect>`;
      }).join('');
      const labels = items.slice(0, 18).map((row, i) => {
        const x = pad.left + (i + .5) * plotW / Math.max(items.length, 1);
        return `<text x="${x}" y="${height - 18}" fill="#637083" font-size="11" text-anchor="middle">${row.run_id}</text>`;
      }).join('');
      svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
      svg.innerHTML = `${grid}<line class="axis" x1="${pad.left}" y1="${pad.top + plotH}" x2="${width - pad.right}" y2="${pad.top + plotH}"/>${bars}${labels}<text x="10" y="20" fill="#637083" font-size="12">${activeMetric}</text>`;
    }

    function renderVisualization() {
      const container = document.getElementById('visualization');
      const history = histories[activeRun] || [];
      if (!history.length) {
        container.innerHTML = '<div class="empty">暂无时序数据。请确认 run 目录包含 time_history.csv。</div>';
        return;
      }
      const hasWheelSeries = history.some(row => numeric(row.wheel_speed_norm_rad_s) || numeric(row.wheel_momentum_fraction) || numeric(row.allocation_error_norm_nm));
      const hasDisturbanceSeries = history.some(row => numeric(row.disturbance_torque_x_nm) || numeric(row.disturbance_torque_y_nm) || numeric(row.disturbance_torque_z_nm));
      const disturbanceTerms = disturbanceBudgetSeries(history);
      const extraPlots = [
        hasWheelSeries ? `
            <div><div class="plot-title"><strong>轮速与动量管理</strong><span>wheel speed / momentum / allocation</span></div><svg class="line-chart" id="wheel-chart"></svg></div>
        ` : '',
        hasDisturbanceSeries ? `
            <div><div class="plot-title"><strong>扰动力矩预算</strong><span>disturbance torque x / y / z</span></div><svg class="line-chart" id="disturbance-chart"></svg></div>
        ` : '',
        disturbanceTerms.length ? `
            <div><div class="plot-title"><strong>环境扰动分解</strong><span>gravity-gradient / magnetic / aero / srp</span></div><svg class="line-chart" id="disturbance-budget-chart"></svg></div>
        ` : '',
      ].join('');
      container.innerHTML = `
        <div class="viz-grid">
          <div class="animation-panel">
            <div class="plot-title"><strong>姿态误差动画</strong><span id="anim-state">已就绪</span></div>
            <svg class="sat-stage" viewBox="0 0 320 220" role="img" aria-label="姿态误差动画">
              <line x1="24" y1="110" x2="296" y2="110" stroke="#cfd7e3" stroke-dasharray="4 5"/>
              <line x1="160" y1="26" x2="160" y2="194" stroke="#cfd7e3" stroke-dasharray="4 5"/>
              <g id="satellite" class="sat-body">
                <rect class="solar-left" x="46" y="88" width="86" height="34" rx="4" fill="#2364aa"/>
                <rect class="solar-right" x="188" y="88" width="86" height="34" rx="4" fill="#087f8c"/>
                <line x1="132" y1="105" x2="188" y2="105" stroke="#65758a" stroke-width="5"/>
                <rect x="128" y="70" width="64" height="70" rx="8" fill="#ffffff" stroke="#718096" stroke-width="3"/>
                <circle cx="160" cy="105" r="16" fill="#edf2f7" stroke="#718096"/>
                <path d="M160 84 L176 105 L160 126 L144 105 Z" fill="#b56b00" opacity=".9"/>
              </g>
            </svg>
            <div class="readout">
              <div><span>仿真时间</span><strong id="anim-time">0 s</strong></div>
              <div><span>姿态误差</span><strong id="anim-error">0 deg</strong></div>
            </div>
          </div>
          <div class="plots">
            <div><div class="plot-title"><strong>姿态误差</strong><span>attitude_error_deg</span></div><svg class="line-chart" id="attitude-chart"></svg></div>
            <div><div class="plot-title"><strong>角速度</strong><span>omega_x/y/z_rad_s</span></div><svg class="line-chart" id="omega-chart"></svg></div>
            <div><div class="plot-title"><strong>控制/执行力矩</strong><span>commanded 与 applied torque</span></div><svg class="line-chart" id="torque-chart"></svg></div>
            ${extraPlots}
          </div>
        </div>`;
      drawLineChart('attitude-chart', history, [
        {key: 'attitude_error_deg', label: '姿态误差 deg', color: '#2364aa'},
      ]);
      drawLineChart('omega-chart', history, [
        {key: 'omega_x_rad_s', label: 'omega x', color: '#2364aa'},
        {key: 'omega_y_rad_s', label: 'omega y', color: '#087f8c'},
        {key: 'omega_z_rad_s', label: 'omega z', color: '#b56b00'},
      ]);
      drawLineChart('torque-chart', history, [
        {key: 'commanded_torque_x_nm', label: 'cmd x', color: '#6f4aa8'},
        {key: 'applied_torque_x_nm', label: 'act x', color: '#b42318'},
      ]);
      if (hasWheelSeries) {
        drawLineChart('wheel-chart', history, [
          {key: 'wheel_speed_norm_rad_s', label: 'wheel speed norm', color: '#087f8c'},
          {key: 'wheel_momentum_fraction', label: 'momentum fraction', color: '#b56b00'},
          {key: 'allocation_error_norm_nm', label: 'allocation error', color: '#6f4aa8'},
        ]);
      }
      if (hasDisturbanceSeries) {
        drawLineChart('disturbance-chart', history, [
          {key: 'disturbance_torque_x_nm', label: 'dist x', color: '#ab2d2d'},
          {key: 'disturbance_torque_y_nm', label: 'dist y', color: '#2364aa'},
          {key: 'disturbance_torque_z_nm', label: 'dist z', color: '#087f8c'},
        ]);
      }
      if (disturbanceTerms.length) {
        drawLineChart('disturbance-budget-chart', history, disturbanceTerms);
      }
      updateAnimationFrame(0);
    }

    function drawLineChart(id, history, series) {
      const svg = document.getElementById(id);
      const width = svg.clientWidth || 760;
      const height = svg.clientHeight || 220;
      const pad = {left: 48, right: 16, top: 14, bottom: 34};
      const xs = history.map(row => Number(row.time_s)).filter(value => !Number.isNaN(value));
      const values = series.flatMap(s => history.map(row => Number(row[s.key]))).filter(value => !Number.isNaN(value));
      if (!xs.length || !values.length) {
        svg.innerHTML = '<text x="18" y="34" fill="#637083">暂无可绘制数据</text>';
        return;
      }
      const minX = Math.min(...xs);
      const maxX = Math.max(...xs);
      const minY = Math.min(...values, 0);
      const maxY = Math.max(...values, 0);
      const spanX = Math.max(maxX - minX, 1e-9);
      const spanY = Math.max(maxY - minY, 1e-9);
      const plotW = width - pad.left - pad.right;
      const plotH = height - pad.top - pad.bottom;
      const xScale = x => pad.left + (x - minX) / spanX * plotW;
      const yScale = y => pad.top + plotH - (y - minY) / spanY * plotH;
      const grid = [0, .25, .5, .75, 1].map(t => {
        const y = pad.top + plotH * t;
        return `<line class="grid" x1="${pad.left}" y1="${y}" x2="${width - pad.right}" y2="${y}"/>`;
      }).join('');
      const paths = series.map(s => {
        const points = history.map(row => {
          const x = Number(row.time_s);
          const y = Number(row[s.key]);
          return Number.isNaN(x) || Number.isNaN(y) ? null : `${xScale(x)},${yScale(y)}`;
        }).filter(Boolean);
        if (!points.length) return '';
        return `<polyline points="${points.join(' ')}" fill="none" stroke="${s.color}" stroke-width="2"><title>${s.label}</title></polyline>`;
      }).join('');
      const legend = series.map((s, i) => `<text x="${pad.left + i * 112}" y="14" fill="${s.color}" font-size="11">${s.label}</text>`).join('');
      svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
      svg.innerHTML = `${grid}<line class="axis" x1="${pad.left}" y1="${pad.top + plotH}" x2="${width - pad.right}" y2="${pad.top + plotH}"/>${paths}${legend}<text x="${width - 62}" y="${height - 10}" fill="#637083" font-size="11">time_s</text><text x="8" y="28" fill="#637083" font-size="11">${fmt(maxY)}</text><text x="8" y="${height - 38}" fill="#637083" font-size="11">${fmt(minY)}</text>`;
    }

    function updateAnimationFrame(idx) {
      const history = histories[activeRun] || [];
      if (!history.length) return;
      animationIndex = Math.max(0, Math.min(idx, history.length - 1));
      const row = history[animationIndex];
      const error = Number(row.attitude_error_deg || 0);
      const omega = Number(row.omega_z_rad_s || 0);
      const angle = Math.max(-60, Math.min(60, error * 1.35)) * (omega < 0 ? -1 : 1);
      const satellite = document.getElementById('satellite');
      if (satellite) satellite.style.transform = `rotate(${angle}deg)`;
      const timeEl = document.getElementById('anim-time');
      const errorEl = document.getElementById('anim-error');
      if (timeEl) timeEl.textContent = `${fmt(row.time_s)} s`;
      if (errorEl) errorEl.textContent = `${fmt(error)} deg`;
    }

    function toggleAnimation() {
      const button = document.getElementById('play');
      if (animationTimer) {
        clearInterval(animationTimer);
        animationTimer = null;
        button.textContent = '播放动画';
        const state = document.getElementById('anim-state');
        if (state) state.textContent = '已暂停';
        return;
      }
      const history = histories[activeRun] || [];
      if (!history.length) return;
      button.textContent = '暂停动画';
      const state = document.getElementById('anim-state');
      if (state) state.textContent = '播放中';
      animationTimer = setInterval(() => {
        updateAnimationFrame((animationIndex + 1) % history.length);
      }, 80);
    }

    function resetAnimation() {
      if (animationTimer) toggleAnimation();
      updateAnimationFrame(0);
      const state = document.getElementById('anim-state');
      if (state) state.textContent = '已回到起点';
    }

    function renderRuns(items) {
      if (!items.length) {
        document.getElementById('runs').innerHTML = '<div class="empty">当前筛选条件下没有 run。</div>';
        return;
      }
      const params = index.parameter_columns || [];
      const metrics = metricColumns.slice(0, 5);
      const columns = ['run_id', 'accepted', ...params, ...metrics, 'output_dir'];
      const header = columns.map(name => `<th title="${name}">${name}</th>`).join('');
      const body = items.map(row => `<tr>${columns.map(name => {
        if (name === 'accepted') {
          const fail = String(row[name]).toLowerCase() !== 'true';
          return `<td><span class="status ${fail ? 'fail' : ''}"><span class="dot"></span>${fail ? '失败' : '通过'}</span></td>`;
        }
        if (name === 'output_dir' && row[name]) {
          return `<td title="${row[name]}"><a href="${row.dashboard_output_href || row[name] + '/README.md'}">${row[name]}</a></td>`;
        }
        return `<td title="${row[name] ?? ''}">${htmlEscape(fmt(row[name]) || row[name] || '')}</td>`;
      }).join('')}</tr>`).join('');
      document.getElementById('runs').innerHTML = `<table><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table>`;
    }

    function acceptanceReasonLabel(reason) {
      const mapping = {
        max_final_error_deg: '末端误差超限',
        max_rms_error_deg: 'RMS 误差超限',
        max_peak_torque_nm: '峰值力矩超限',
      };
      return mapping[reason] || reason || '未说明';
    }

    function failureReasons(row) {
      const raw = String(row.failed_acceptance || '').trim();
      if (!raw) return [];
      return raw.split(';').map(item => item.trim()).filter(Boolean);
    }

    function aggregateFailureReasons() {
      const counts = new Map();
      rows.forEach(row => {
        failureReasons(row).forEach(reason => {
          counts.set(reason, (counts.get(reason) || 0) + 1);
        });
      });
      return [...counts.entries()].sort((a, b) => b[1] - a[1]);
    }

    function parameterLabel(name) {
      const raw = String(name || '').replace(/^param_/, '');
      const labels = {
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
      return labels[raw] || raw;
    }

    function disturbanceTermLabel(key) {
      const raw = String(key || '').replace(/_torque_norm_nm$/, '');
      const labels = {
        gravity_gradient: '重力梯度',
        residual_magnetic: '残余磁矩',
        aerodynamic: '气动',
        solar_pressure: '太阳压',
      };
      return labels[raw] || raw;
    }

    function disturbanceBudgetSeries(history) {
      const row = (history || [])[0] || {};
      const columns = Object.keys(row).filter(key => key.endsWith('_torque_norm_nm') && !key.startsWith('disturbance_'));
      const colors = ['#6f4aa8', '#ab2d2d', '#2364aa', '#087f8c', '#b56b00', '#247a48'];
      return columns.map((key, index) => ({
        key,
        label: disturbanceTermLabel(key),
        color: colors[index % colors.length],
      })).filter(series => history.some(item => numeric(item[series.key])));
    }

    function parameterEntriesForRow(row) {
      return Object.entries(row || {}).filter(([key, value]) =>
        key.startsWith('param_') && value !== null && value !== undefined && value !== ''
      );
    }

    function parameterDiffSummary(best, worst) {
      const bestParams = new Map(parameterEntriesForRow(best));
      const diffs = parameterEntriesForRow(worst)
        .filter(([key, value]) => String(bestParams.get(key) ?? '') !== String(value ?? ''))
        .slice(0, 3);
      if (!diffs.length) return '';
      return diffs.map(([key, value]) => `${parameterLabel(key)}=${value}`).join('，');
    }

    function experimentTheme() {
      const params = index.parameter_columns || [];
      if (params.includes('param_system.controller')) return '控制器 benchmark';
      if (params.includes('param_system.environment')) return '环境敏感性';
      if (params.includes('param_system.disturbance_profile')) return '扰动分解';
      if (params.includes('param_sensors.gyro.noise_std_rad_s')) return '测量敏感性';
      if (params.includes('param_actuators.reaction_wheels.momentum_gain')) return '轮速管理';
      if (params.includes('param_actuators.reaction_wheels.max_torque_nm')) return '执行器边界';
      if (params.includes('param_controller.pd_kp') || params.includes('param_controller.pd_kd')) return '控制器整定';
      if (params.includes('param_time.seed') || params.includes('param_monte_carlo.sample')) return '鲁棒性';
      if ((data.timeline?.timeline || []).length) return '任务模式切换';
      return '通用实验';
    }

    function experimentObservation(best, worst) {
      if (best['param_system.controller']) {
        return `当前最佳控制器为 ${best['param_system.controller']}，可重点对比它与 ${worst['param_system.controller'] || '另一控制器'} 的误差与力矩差异。`;
      }
      if (best['param_system.environment']) {
        return `当前最佳环境配置为 ${best['param_system.environment']}，建议重点比较 orbital 与 zero 环境下的误差和扰动力矩预算差异。`;
      }
      if (best['param_system.disturbance_profile']) {
        return `当前最佳扰动配置为 ${best['param_system.disturbance_profile']}，建议继续比较不同扰动模板下主导扰动项和误差退化顺序。`;
      }
      if (best['param_sensors.gyro.noise_std_rad_s']) {
        return `当前最佳测量质量对应的陀螺噪声为 ${best['param_sensors.gyro.noise_std_rad_s']} rad/s，建议关注最高噪声档位下的误差退化。`;
      }
      if (best['param_actuators.reaction_wheels.momentum_gain'] !== undefined && best['param_actuators.reaction_wheels.momentum_gain'] !== '') {
        return `当前最优动量管理增益为 ${best['param_actuators.reaction_wheels.momentum_gain']}，建议结合轮速与分配误差一并判断。`;
      }
      if (best['param_actuators.reaction_wheels.max_torque_nm']) {
        return `当前最优执行器能力为 ${best['param_actuators.reaction_wheels.max_torque_nm']} Nm，可继续联动饱和与轮速变化一起判断。`;
      }
      return `当前最佳 run 为 ${best.run_id || index.best_run_id || '—'}，最佳末端误差 ${fmt(best.final_error_deg)} deg。`;
    }

    function peakHistoryValue(keys) {
      let peak = null;
      Object.values(histories).forEach(history => {
        (history || []).forEach(row => {
          const values = keys.map(key => Number(row[key]));
          if (values.some(value => Number.isNaN(value))) return;
          const norm = Math.sqrt(values.reduce((sum, value) => sum + value * value, 0));
          peak = peak === null ? norm : Math.max(peak, norm);
        });
      });
      return peak;
    }

    function peakHistoryScalar(key) {
      let peak = null;
      Object.values(histories).forEach(history => {
        (history || []).forEach(row => {
          const value = Number(row[key]);
          if (Number.isNaN(value)) return;
          peak = peak === null ? value : Math.max(peak, value);
        });
      });
      return peak;
    }

    function dominantDisturbanceText() {
      const terms = disturbanceBudgetSeries(histories[activeRun] || []);
      if (!terms.length) return '当前结果未提供环境扰动分解列。';
      const peaks = terms
        .map(series => ({label: series.label, peak: peakHistoryScalar(series.key)}))
        .filter(item => item.peak !== null);
      if (!peaks.length) return '当前结果未提供可用的环境扰动分解数据。';
      peaks.sort((a, b) => Number(b.peak) - Number(a.peak));
      const lead = peaks[0];
      const rest = peaks.slice(1, 3).map(item => `${item.label} ${fmt(item.peak)} N m`).join('，');
      return `${lead.label} 当前峰值最高，为 ${fmt(lead.peak)} N m。${rest ? `其后依次为 ${rest}。` : ''}`;
    }

    function worstRunExplanation(best, worst) {
      if (!worst || !worst.run_id) return '当前没有可解释的最差 run。';
      const reasons = failureReasons(worst);
      const diffText = parameterDiffSummary(best, worst);
      const theme = experimentTheme();
      const parts = [`最差 run 为 ${worst.run_id}，末端误差 ${fmt(worst.final_error_deg)} deg。`];
      if (reasons.length) {
        parts.push(`它的主要验收问题是 ${reasons.map(acceptanceReasonLabel).join('、')}。`);
      }
      if (diffText) {
        parts.push(`相对最佳 run，它的关键参数差异是 ${diffText}。`);
      } else if (worst.run_id !== best.run_id) {
        parts.push('它与最佳 run 没有明显的显式参数差异，更可能是随机样本、环境扰动或运行边界造成的退化。');
      }
      if (theme === '环境敏感性' && worst['param_system.environment']) {
        parts.push(`当前最差环境配置为 ${worst['param_system.environment']}，建议重点联动环境扰动分解图一起看。`);
      } else if (theme === '扰动分解' && worst['param_system.disturbance_profile']) {
        parts.push(`当前最差扰动模板为 ${worst['param_system.disturbance_profile']}，建议继续结合主导扰动项和姿态误差曲线确认是哪类环境项在放大退化。`);
      } else if (theme === '鲁棒性' && (worst['param_time.seed'] || worst['param_monte_carlo.sample'])) {
        parts.push('这更像是随机样本触发的边界工况，适合回看该 run 的误差时序和扰动峰值。');
      } else if (theme === '测量敏感性' && worst['param_sensors.gyro.noise_std_rad_s']) {
        parts.push(`当前最差噪声档位为 ${worst['param_sensors.gyro.noise_std_rad_s']} rad/s，可继续确认误差是否在观测链上被放大。`);
      }
      return parts.join('');
    }

    function timelineSummary() {
      const timeline = data.timeline?.timeline || [];
      if (!timeline.length) return '暂无任务模式时间线。';
      return `共 ${timeline.length} 段，模式顺序：${timeline.map(item => item.mode || item.name || 'mode').join(' -> ')}。`;
    }

    function renderDiagnostics() {
      const host = document.getElementById('diagnostics');
      const failures = aggregateFailureReasons();
      const peakOmega = peakHistoryValue(['omega_x_rad_s', 'omega_y_rad_s', 'omega_z_rad_s']);
      const peakAppliedTorque = peakHistoryValue(['applied_torque_x_nm', 'applied_torque_y_nm', 'applied_torque_z_nm']);
      const peakDisturbance = peakHistoryValue(['disturbance_torque_x_nm', 'disturbance_torque_y_nm', 'disturbance_torque_z_nm']);
      const best = rows.find(row => row.run_id === index.best_run_id) || rows[0] || {};
      const worst = [...rows].sort((a, b) => Number(b.final_error_deg || 0) - Number(a.final_error_deg || 0))[0] || {};
      const parameterText = (index.parameter_columns || []).map(parameterLabel).join(' / ') || '未显式给出';
      host.innerHTML = `
        <div class="details">
          <div><strong>实验导读</strong><div class="insight-grid">
            <div><strong>实验类型</strong><div class="meta">${experimentTheme()} · 变量 ${parameterText}</div></div>
            <div><strong>当前观察</strong><div class="meta">${experiment.metadata?.description || '当前实验重点是比较不同配置对闭环稳定性和控制性能的影响。'} ${experimentObservation(best, worst)}</div></div>
          </div></div>
          <div><strong>验收失败原因</strong><div class="tags">${failures.length ? failures.map(([reason, count]) => `<span class="tag">${acceptanceReasonLabel(reason)} × ${count}</span>`).join('') : '<span class="tag">当前没有失败 run</span>'}</div></div>
          <div><strong>动态峰值摘要</strong><div class="meta">角速度峰值 ${fmt(peakOmega)} rad/s，执行力矩峰值 ${fmt(peakAppliedTorque)} N m，扰动力矩峰值 ${fmt(peakDisturbance)} N m。</div></div>
          <div><strong>主导扰动项</strong><div class="meta">${dominantDisturbanceText()}</div></div>
          <div><strong>最佳 run 摘要</strong><div class="meta">${best.run_id || '—'} / 末端误差 ${fmt(best.final_error_deg)} deg / RMS ${fmt(best.rms_error_deg)} deg / 峰值力矩 ${fmt(best.peak_torque_nm)} N m</div></div>
          <div><strong>最差 run 解释</strong><div class="meta">${worstRunExplanation(best, worst)}</div></div>
          <div><strong>任务与调度</strong><div class="meta">${timelineSummary()} ${data.runtime?.event_count ? `运行时共 ${data.runtime.event_count} 个事件。` : '暂无运行时调度。'}</div></div>
        </div>
      `;
    }

    function renderTimeline() {
      const container = document.getElementById('timeline');
      const timeline = data.timeline?.timeline || [];
      const duration = Number(data.timeline?.duration_s || Math.max(...timeline.map(item => Number(item.stop_s || 0)), 0));
      if (!timeline.length || !duration) {
        container.innerHTML = '<div class="empty">暂无任务模式时间线。</div>';
        return;
      }
      container.innerHTML = `<div class="timeline">${timeline.map((item, i) => {
        const left = Number(item.start_s || 0) / duration * 100;
        const width = (Number(item.stop_s || 0) - Number(item.start_s || 0)) / duration * 100;
        const color = ['#6f4aa8', '#087f8c', '#2364aa', '#a45f00', '#247a48'][i % 5];
        return `<div class="segment"><strong title="${item.mode}">${item.mode}</strong><div class="track"><div class="fill" style="left:${left}%;width:${width}%;background:${color}"></div></div><span class="time">${fmt(item.start_s)}-${fmt(item.stop_s)} s</span></div>`;
      }).join('')}</div>`;
    }

    function renderRuntime() {
      const container = document.getElementById('runtime');
      const events = data.runtime?.events || [];
      if (!events.length) {
        container.innerHTML = '<div class="empty">暂无运行时调度。</div>';
        return;
      }
      const sample = events.slice(0, 80);
      container.innerHTML = `<div class="runtime-list"><table><thead><tr><th>time_s</th><th>task</th><th>module</th><th>role</th></tr></thead><tbody>${sample.map(event => `<tr><td>${fmt(event.time_s)}</td><td>${event.task}</td><td>${event.module}</td><td>${event.role}</td></tr>`).join('')}</tbody></table></div>`;
    }

    function renderFiles() {
      const files = [
        ['实验报告 README.md', 'README.md'],
        ['机器索引 index.json', 'index.json'],
        ['汇总指标 summary_metrics.csv', 'summary_metrics.csv'],
        ['实验清单 experiment_manifest.json', 'experiment_manifest.json'],
        ['汇报摘要 REPORT_SUMMARY.md', index.report_summary],
        ['论文摘要 PAPER_SUMMARY.md', index.paper_summary],
        [index.runtime_schedule, index.runtime_schedule],
        [index.mode_timeline, index.mode_timeline],
      ].filter(item => item[0]);
      document.getElementById('files').innerHTML = files.map(([label, href]) => `<div><a href="${href}">${label}</a></div>`).join('');
    }

    function htmlEscape(value) {
      return String(value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    }

    function render() {
      const items = filteredRows();
      renderChart(items);
      renderRuns(items);
    }
    document.getElementById('search').addEventListener('input', render);
    document.getElementById('accepted').addEventListener('change', render);
    metricSelect.addEventListener('change', event => { activeMetric = event.target.value; render(); });
    runSelect.addEventListener('change', event => {
      activeRun = event.target.value;
      resetAnimation();
      renderVisualization();
    });
    document.getElementById('play').addEventListener('click', toggleAnimation);
    document.getElementById('reset').addEventListener('click', resetAnimation);
    render();
    renderDiagnostics();
    renderVisualization();
    renderTimeline();
    renderRuntime();
    renderFiles();
  </script>
</body>
</html>
"""
    return template.replace("__TITLE__", html.escape(title)).replace("__DATA__", data)
