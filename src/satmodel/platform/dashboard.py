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


def _render_dashboard(payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, default=str).replace("</", "<\\/")
    experiment = payload.get("manifest", {}).get("experiment", {})
    title = experiment.get("metadata", {}).get("name") or "satmodel experiment"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)} dashboard</title>
  <style>
    :root {{
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
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }}
    header {{
      background: #ffffff;
      border-bottom: 1px solid var(--line);
      padding: 18px 24px 14px;
      position: sticky;
      top: 0;
      z-index: 4;
    }}
    .topline {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      flex-wrap: wrap;
    }}
    h1 {{
      margin: 0;
      font-size: 22px;
      line-height: 1.2;
      font-weight: 720;
    }}
    .meta {{
      color: var(--muted);
      display: flex;
      gap: 14px;
      flex-wrap: wrap;
      margin-top: 8px;
    }}
    main {{
      padding: 18px 24px 32px;
      max-width: 1500px;
      margin: 0 auto;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(5, minmax(130px, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }}
    .stat {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      min-height: 78px;
    }}
    .stat span {{
      color: var(--muted);
      display: block;
      font-size: 12px;
    }}
    .stat strong {{
      display: block;
      font-size: 24px;
      margin-top: 4px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .layout {{
      display: grid;
      grid-template-columns: minmax(0, 1.45fr) minmax(340px, 0.8fr);
      gap: 14px;
      align-items: start;
    }}
    section {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      margin-bottom: 14px;
      overflow: hidden;
    }}
    h2 {{
      margin: 0 0 12px;
      font-size: 15px;
      font-weight: 700;
    }}
    .toolbar {{
      display: grid;
      grid-template-columns: minmax(180px, 1fr) 180px 160px;
      gap: 10px;
      margin-bottom: 12px;
    }}
    input, select {{
      width: 100%;
      min-height: 36px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 7px 9px;
      color: var(--ink);
      background: #fff;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }}
    th, td {{
      border-bottom: 1px solid #e7ebf1;
      text-align: left;
      padding: 8px 7px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      font-size: 13px;
    }}
    th {{
      color: var(--muted);
      font-weight: 650;
      background: #fafbfc;
      position: sticky;
      top: 82px;
      z-index: 2;
    }}
    .status {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-weight: 650;
    }}
    .dot {{
      width: 8px;
      height: 8px;
      border-radius: 50%;
      display: inline-block;
      background: var(--ok);
    }}
    .status.fail .dot {{ background: var(--bad); }}
    .chart {{
      width: 100%;
      height: 250px;
      display: block;
      border: 1px solid #e7ebf1;
      border-radius: 6px;
      background: #fbfcfe;
    }}
    .axis, .grid {{ stroke: #ccd3df; stroke-width: 1; }}
    .grid {{ opacity: .7; }}
    .bar {{ fill: var(--accent); }}
    .bar.fail {{ fill: var(--bad); }}
    .bar.best {{ fill: var(--teal); }}
    .timeline {{
      display: grid;
      gap: 8px;
    }}
    .segment {{
      display: grid;
      grid-template-columns: 110px minmax(0, 1fr) 90px;
      gap: 8px;
      align-items: center;
    }}
    .track {{
      height: 24px;
      background: #edf1f6;
      border-radius: 6px;
      overflow: hidden;
      position: relative;
    }}
    .fill {{
      height: 100%;
      min-width: 2px;
      position: absolute;
      background: var(--violet);
    }}
    .runtime-list {{
      max-height: 310px;
      overflow: auto;
      border: 1px solid #e7ebf1;
      border-radius: 6px;
    }}
    .runtime-list table th {{ top: 0; }}
    .empty {{
      color: var(--muted);
      padding: 18px;
      border: 1px dashed var(--line);
      border-radius: 8px;
      background: #fbfcfe;
    }}
    a {{ color: var(--accent); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    @media (max-width: 980px) {{
      main {{ padding: 14px; }}
      .stats, .layout, .toolbar {{ grid-template-columns: 1fr; }}
      th {{ position: static; }}
      .segment {{ grid-template-columns: 90px minmax(0, 1fr); }}
      .segment .time {{ grid-column: 1 / -1; }}
    }}
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
          <h2>Run Metrics</h2>
          <div class="toolbar">
            <input id="search" type="search" placeholder="Filter runs, parameters, status">
            <select id="metric"></select>
            <select id="accepted">
              <option value="all">All runs</option>
              <option value="accepted">Accepted</option>
              <option value="failed">Failed</option>
            </select>
          </div>
          <svg class="chart" id="chart" role="img" aria-label="Metric chart"></svg>
        </section>
        <section>
          <h2>Runs</h2>
          <div id="runs"></div>
        </section>
      </div>
      <div>
        <section>
          <h2>Mode Timeline</h2>
          <div id="timeline"></div>
        </section>
        <section>
          <h2>Runtime Schedule</h2>
          <div id="runtime"></div>
        </section>
        <section>
          <h2>Files</h2>
          <div id="files"></div>
        </section>
      </div>
    </div>
  </main>
  <script id="payload" type="application/json">{data}</script>
  <script>
    const data = JSON.parse(document.getElementById('payload').textContent);
    const index = data.index || {{}};
    const manifest = data.manifest || {{}};
    const experiment = manifest.experiment || {{}};
    const rows = data.rows || [];
    const numeric = value => value !== '' && value !== null && value !== undefined && !Number.isNaN(Number(value));
    const fmt = value => numeric(value) ? Number(value).toPrecision(5).replace(/\\.0+$/, '') : '';
    const metricColumns = (index.metric_columns || []).filter(name => rows.some(row => numeric(row[name])));
    let activeMetric = metricColumns.includes('final_error_deg') ? 'final_error_deg' : metricColumns[0];

    document.getElementById('title').textContent = experiment.metadata?.name || 'satmodel experiment';
    document.getElementById('created').textContent = `Generated ${{(data.created_at_utc || '').slice(0, 19)}}`;
    document.getElementById('version').textContent = `satmodel ${{data.satmodel_version || ''}}`;

    const statItems = [
      ['Runs', index.run_count ?? rows.length],
      ['Accepted', index.accepted_count ?? rows.filter(row => row.accepted === 'True').length],
      ['Failed', index.failed_count ?? rows.filter(row => row.accepted === 'False').length],
      ['Acceptance', `${{Math.round((index.acceptance_rate ?? 0) * 1000) / 10}}%`],
      ['Best Run', index.best_run_id || ''],
    ];
    document.getElementById('stats').innerHTML = statItems.map(([label, value]) =>
      `<div class="stat"><span>${{label}}</span><strong title="${{value}}">${{value}}</strong></div>`
    ).join('');

    const metricSelect = document.getElementById('metric');
    metricSelect.innerHTML = metricColumns.map(name => `<option value="${{name}}">${{name}}</option>`).join('');
    if (activeMetric) metricSelect.value = activeMetric;

    function filteredRows() {{
      const q = document.getElementById('search').value.trim().toLowerCase();
      const accepted = document.getElementById('accepted').value;
      return rows.filter(row => {{
        const ok = String(row.accepted).toLowerCase() === 'true';
        if (accepted === 'accepted' && !ok) return false;
        if (accepted === 'failed' && ok) return false;
        if (!q) return true;
        return Object.values(row).some(value => String(value).toLowerCase().includes(q));
      }});
    }}

    function renderChart(items) {{
      const svg = document.getElementById('chart');
      svg.innerHTML = '';
      if (!activeMetric || !items.length) {{
        svg.innerHTML = '<text x="20" y="38" fill="#637083">No metric data</text>';
        return;
      }}
      const width = svg.clientWidth || 800;
      const height = svg.clientHeight || 250;
      const pad = {{left: 44, right: 14, top: 18, bottom: 48}};
      const values = items.map(row => Number(row[activeMetric])).filter(value => !Number.isNaN(value));
      const max = Math.max(...values, 1e-9);
      const min = Math.min(...values, 0);
      const span = Math.max(max - min, 1e-9);
      const plotW = width - pad.left - pad.right;
      const plotH = height - pad.top - pad.bottom;
      const barW = Math.max(4, plotW / Math.max(items.length, 1) * 0.72);
      const bestId = index.best_run_id;
      const grid = [0, .25, .5, .75, 1].map(t => {{
        const y = pad.top + plotH * t;
        return `<line class="grid" x1="${{pad.left}}" y1="${{y}}" x2="${{width - pad.right}}" y2="${{y}}"/>`;
      }}).join('');
      const bars = items.map((row, i) => {{
        const value = Number(row[activeMetric]);
        const x = pad.left + (i + .14) * plotW / Math.max(items.length, 1);
        const h = Number.isNaN(value) ? 0 : Math.max(1, ((value - min) / span) * plotH);
        const y = pad.top + plotH - h;
        const cls = row.run_id === bestId ? 'bar best' : String(row.accepted).toLowerCase() === 'true' ? 'bar' : 'bar fail';
        return `<rect class="${{cls}}" x="${{x}}" y="${{y}}" width="${{barW}}" height="${{h}}"><title>${{row.run_id}} ${{activeMetric}}=${{fmt(value)}}</title></rect>`;
      }}).join('');
      const labels = items.slice(0, 18).map((row, i) => {{
        const x = pad.left + (i + .5) * plotW / Math.max(items.length, 1);
        return `<text x="${{x}}" y="${{height - 18}}" fill="#637083" font-size="11" text-anchor="middle">${{row.run_id}}</text>`;
      }}).join('');
      svg.setAttribute('viewBox', `0 0 ${{width}} ${{height}}`);
      svg.innerHTML = `${{grid}}<line class="axis" x1="${{pad.left}}" y1="${{pad.top + plotH}}" x2="${{width - pad.right}}" y2="${{pad.top + plotH}}"/>${{bars}}${{labels}}<text x="10" y="20" fill="#637083" font-size="12">${{activeMetric}}</text>`;
    }}

    function renderRuns(items) {{
      if (!items.length) {{
        document.getElementById('runs').innerHTML = '<div class="empty">No runs match the current filters.</div>';
        return;
      }}
      const params = index.parameter_columns || [];
      const metrics = metricColumns.slice(0, 5);
      const columns = ['run_id', 'accepted', ...params, ...metrics, 'output_dir'];
      const header = columns.map(name => `<th title="${{name}}">${{name}}</th>`).join('');
      const body = items.map(row => `<tr>${{columns.map(name => {{
        if (name === 'accepted') {{
          const fail = String(row[name]).toLowerCase() !== 'true';
          return `<td><span class="status ${{fail ? 'fail' : ''}}"><span class="dot"></span>${{fail ? 'Failed' : 'Accepted'}}</span></td>`;
        }}
        if (name === 'output_dir' && row[name]) {{
          return `<td title="${{row[name]}}"><a href="${{row.dashboard_output_href || row[name] + '/README.md'}}">${{row[name]}}</a></td>`;
        }}
        return `<td title="${{row[name] ?? ''}}">${{htmlEscape(fmt(row[name]) || row[name] || '')}}</td>`;
      }}).join('')}}</tr>`).join('');
      document.getElementById('runs').innerHTML = `<table><thead><tr>${{header}}</tr></thead><tbody>${{body}}</tbody></table>`;
    }}

    function renderTimeline() {{
      const container = document.getElementById('timeline');
      const timeline = data.timeline?.timeline || [];
      const duration = Number(data.timeline?.duration_s || Math.max(...timeline.map(item => Number(item.stop_s || 0)), 0));
      if (!timeline.length || !duration) {{
        container.innerHTML = '<div class="empty">No mode timeline.</div>';
        return;
      }}
      container.innerHTML = `<div class="timeline">${{timeline.map((item, i) => {{
        const left = Number(item.start_s || 0) / duration * 100;
        const width = (Number(item.stop_s || 0) - Number(item.start_s || 0)) / duration * 100;
        const color = ['#6f4aa8', '#087f8c', '#2364aa', '#a45f00', '#247a48'][i % 5];
        return `<div class="segment"><strong title="${{item.mode}}">${{item.mode}}</strong><div class="track"><div class="fill" style="left:${{left}}%;width:${{width}}%;background:${{color}}"></div></div><span class="time">${{fmt(item.start_s)}}-${{fmt(item.stop_s)}} s</span></div>`;
      }}).join('')}}</div>`;
    }}

    function renderRuntime() {{
      const container = document.getElementById('runtime');
      const events = data.runtime?.events || [];
      if (!events.length) {{
        container.innerHTML = '<div class="empty">No runtime schedule.</div>';
        return;
      }}
      const sample = events.slice(0, 80);
      container.innerHTML = `<div class="runtime-list"><table><thead><tr><th>time_s</th><th>task</th><th>module</th><th>role</th></tr></thead><tbody>${{sample.map(event => `<tr><td>${{fmt(event.time_s)}}</td><td>${{event.task}}</td><td>${{event.module}}</td><td>${{event.role}}</td></tr>`).join('')}}</tbody></table></div>`;
    }}

    function renderFiles() {{
      const files = [
        ['README.md', 'README.md'],
        ['index.json', 'index.json'],
        ['summary_metrics.csv', 'summary_metrics.csv'],
        ['experiment_manifest.json', 'experiment_manifest.json'],
        [index.runtime_schedule, index.runtime_schedule],
        [index.mode_timeline, index.mode_timeline],
      ].filter(item => item[0]);
      document.getElementById('files').innerHTML = files.map(([label, href]) => `<div><a href="${{href}}">${{label}}</a></div>`).join('');
    }}

    function htmlEscape(value) {{
      return String(value).replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));
    }}

    function render() {{
      const items = filteredRows();
      renderChart(items);
      renderRuns(items);
    }}
    document.getElementById('search').addEventListener('input', render);
    document.getElementById('accepted').addEventListener('change', render);
    metricSelect.addEventListener('change', event => {{ activeMetric = event.target.value; render(); }});
    render();
    renderTimeline();
    renderRuntime();
    renderFiles();
  </script>
</body>
</html>
"""
