"""Experiment-level report builders."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from satmodel._version import __version__
from satmodel.platform.dashboard import build_dashboard


class ReportBuilder:
    """Build experiment-level CSV, JSON, and Markdown outputs."""

    def __init__(self, summary):
        self.summary = summary

    def acceptance_summary(self) -> dict[str, int | float]:
        rows = self.summary.rows
        run_count = len(rows)
        accepted_count = sum(1 for row in rows if bool(row.get("accepted", True)))
        failed_count = run_count - accepted_count
        acceptance_rate = accepted_count / run_count if run_count else 0.0
        return {
            "run_count": run_count,
            "accepted_count": accepted_count,
            "failed_count": failed_count,
            "acceptance_rate": acceptance_rate,
        }

    def best_row(self, metric: str = "final_error_deg") -> dict[str, Any] | None:
        if not self.summary.rows:
            return None
        candidates = [row for row in self.summary.rows if metric in row]
        if not candidates:
            return dict(self.summary.rows[0])
        return dict(min(candidates, key=lambda row: self._numeric_value(row.get(metric), default=float("inf"))))

    def worst_row(self, metric: str = "final_error_deg") -> dict[str, Any] | None:
        if not self.summary.rows:
            return None
        candidates = [row for row in self.summary.rows if metric in row]
        if not candidates:
            return dict(self.summary.rows[-1])
        return dict(max(candidates, key=lambda row: self._numeric_value(row.get(metric), default=float("-inf"))))

    def parameter_columns(self) -> list[str]:
        return [name for name in self._fieldnames() if name.startswith("param_")]

    def metric_columns(self) -> list[str]:
        metadata = {
            "run_id",
            "scenario",
            "seed",
            "system_builder",
            "controller",
            "environment",
            "fault_count",
            "accepted",
            "failed_acceptance",
            "output_dir",
        }
        columns = []
        for name in self._fieldnames():
            if name in metadata or name.startswith("param_"):
                continue
            if any(self._is_number(row.get(name)) for row in self.summary.rows):
                columns.append(name)
        return columns

    def write_metrics_csv(self, filename: str = "summary_metrics.csv") -> Path:
        path = self.summary.output_dir / filename
        fieldnames = self._fieldnames()
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in self.summary.rows:
                writer.writerow({key: self._cell(row.get(key, "")) for key in fieldnames})
        return path

    def write_study_manifest(self, filename: str = "study_manifest.json") -> Path:
        path = self.summary.output_dir / filename
        payload = {
            "manifest_version": 1,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "satmodel_version": __version__,
            "run_count": len(self.summary.rows),
            "runs": self.summary.rows,
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        return path

    def write_experiment_manifest(self, filename: str = "experiment_manifest.json") -> Path:
        path = self.summary.output_dir / filename
        payload = {
            "manifest_version": 1,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "satmodel_version": __version__,
            "experiment": {} if self.summary.plan is None else self.summary.plan.to_mapping(),
            "run_count": len(self.summary.rows),
            "runs": self.summary.rows,
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        return path

    def write_index(self, filename: str = "index.json") -> Path:
        path = self.summary.output_dir / filename
        acceptance = self.acceptance_summary()
        best = self.best_row()
        runtime_file = "runtime_schedule.json" if self._has_runtime() else None
        mode_file = "mode_timeline.json" if self._has_mission() else None
        payload = {
            "index_version": 1,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "satmodel_version": __version__,
            **acceptance,
            "best_run_id": None if best is None else best.get("run_id"),
            "best_output_dir": None if best is None else best.get("output_dir"),
            "metric_columns": self.metric_columns(),
            "parameter_columns": self.parameter_columns(),
            "runtime_schedule": runtime_file,
            "mode_timeline": mode_file,
            "report_summary": "REPORT_SUMMARY.md",
            "paper_summary": "PAPER_SUMMARY.md",
            "dashboard": "dashboard.html",
            "runs": self.summary.rows,
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        return path

    def write_markdown(self, filename: str = "README.md") -> Path:
        path = self.summary.output_dir / filename
        acceptance = self.acceptance_summary()
        best = self.best_row()
        worst = self.worst_row()
        parameter_columns = self.parameter_columns()
        metric_columns = self.metric_columns()
        title = "satmodel Experiment Summary"
        if self.summary.plan is not None:
            title = f"satmodel Experiment Summary: {self.summary.plan.name}"
        lines = [
            f"# {title}",
            "",
            f"- Runs: `{len(self.summary.rows)}`",
            f"- Accepted: `{acceptance['accepted_count']}`",
            f"- Failed: `{acceptance['failed_count']}`",
            f"- Acceptance rate: `{acceptance['acceptance_rate']:.1%}`",
            f"- Best run: `{self._row_value(best, 'run_id')}`",
            f"- Best final error deg: `{self._format_number(self._row_value(best, 'final_error_deg'))}`",
            f"- Worst run: `{self._row_value(worst, 'run_id')}`",
            f"- Worst final error deg: `{self._format_number(self._row_value(worst, 'final_error_deg'))}`",
            "",
            "## Parameters",
            "",
        ]
        lines.extend(f"- `{column}`" for column in parameter_columns) if parameter_columns else lines.append("- None")
        lines.extend(["", "## Metrics", ""])
        lines.extend(f"- `{column}`" for column in metric_columns) if metric_columns else lines.append("- None")
        lines.extend(
            [
                "",
                "## Runs",
                "",
                "| Run | Scenario | Accepted | Final error deg | RMS error deg | Peak torque N m | Output |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for row in self.summary.rows:
            lines.append(
                "| {run_id} | {scenario} | {accepted} | {final} | {rms} | {peak} | {output} |".format(
                    run_id=row["run_id"],
                    scenario=row["scenario"],
                    accepted=row["accepted"],
                    final=self._format_number(row.get("final_error_deg")),
                    rms=self._format_number(row.get("rms_error_deg")),
                    peak=self._format_number(row.get("peak_torque_nm")),
                    output=row.get("output_dir", ""),
                )
            )
        lines.extend(
            [
                "",
                "## Files",
                "",
                "- `summary_metrics.csv`: one row per run with parameters and metrics.",
                "- `study_manifest.json`: compatibility manifest with raw rows.",
                "- `experiment_manifest.json`: platform experiment manifest with plan metadata.",
                "- `index.json`: compact machine-readable experiment index.",
                "- `REPORT_SUMMARY.md`: report-oriented executive summary template.",
                "- `PAPER_SUMMARY.md`: paper-oriented experiment summary template.",
            ]
        )
        if self._has_runtime():
            lines.append("- `runtime_schedule.json`: expanded process/task/module schedule.")
        if self._has_mission():
            lines.append("- `mode_timeline.json`: mission step and mode intervals.")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def write_report_summary(self, filename: str = "REPORT_SUMMARY.md") -> Path:
        path = self.summary.output_dir / filename
        acceptance = self.acceptance_summary()
        best = self.best_row()
        worst = self.worst_row()
        lines = [
            f"# 汇报摘要: {self._experiment_title()}",
            "",
            "## 一句话结论",
            "",
            self._core_conclusion_text(),
            "",
            "## 结果摘要",
            "",
            f"- 实验名: `{self._experiment_title()}`",
            f"- Run 数: `{acceptance['run_count']}`",
            f"- 通过数: `{acceptance['accepted_count']}`",
            f"- 失败数: `{acceptance['failed_count']}`",
            f"- 通过率: `{acceptance['acceptance_rate']:.1%}`",
            f"- 最佳 run: `{self._row_value(best, 'run_id')}`",
            f"- 最佳末端误差: `{self._format_number(self._row_value(best, 'final_error_deg'))}` deg",
            f"- 最差 run: `{self._row_value(worst, 'run_id')}`",
            f"- 最差末端误差: `{self._format_number(self._row_value(worst, 'final_error_deg'))}` deg",
            "",
            "## 建议汇报结构",
            "",
            "1. 先展示当前实验研究问题和场景边界。",
            "1. 再展示最佳 / 最差 run 与通过率，总结当前是否达到预期。",
            "1. 接着展示代表图、诊断图和边界图，说明差异来自参数、环境、任务还是执行器。",
            "1. 最后回到下一组实验，说明后续是继续整定、扩鲁棒性，还是进入任务或执行器边界。",
            "",
            "## 推荐图与表",
            "",
            f"- 代表图: {self._representative_figure_text()}",
            f"- 诊断图: {self._diagnostic_figure_text()}",
            f"- 边界图: {self._boundary_figure_text()}",
            f"- 建议汇总表: {self._table_recommendation_text()}",
            "",
            "## 文件入口",
            "",
            "- `README.md`: 实验根目录标准摘要。",
            "- `summary_metrics.csv`: 每个 run 的参数和指标汇总。",
            "- `index.json`: 机器可读实验索引。",
            "- `dashboard.html`: 可直接展示、预览和归档的静态结果界面。",
        ]
        if self._has_runtime():
            lines.append("- `runtime_schedule.json`: 运行时调度摘要。")
        if self._has_mission():
            lines.append("- `mode_timeline.json`: 任务模式时间线摘要。")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def write_paper_summary(self, filename: str = "PAPER_SUMMARY.md") -> Path:
        path = self.summary.output_dir / filename
        acceptance = self.acceptance_summary()
        best = self.best_row()
        lines = [
            f"# 论文摘要模板: {self._experiment_title()}",
            "",
            "## 研究问题",
            "",
            self._research_question_text(),
            "",
            "## 实验设置",
            "",
            f"- 实验名: `{self._experiment_title()}`",
            f"- 参数列: {self._column_list_text(self.parameter_columns())}",
            f"- 指标列: {self._column_list_text(self.metric_columns()[:8])}",
            f"- Run 数: `{acceptance['run_count']}`",
            f"- 通过率: `{acceptance['acceptance_rate']:.1%}`",
            "",
            "## 关键结果",
            "",
            f"- 最佳 run: `{self._row_value(best, 'run_id')}`，末端误差 `{self._format_number(self._row_value(best, 'final_error_deg'))}` deg。",
            f"- 核心判断: {self._core_conclusion_text()}",
            f"- 下一步主线: {self._next_step_text()}",
            "",
            "## 建议论文图",
            "",
            f"- 图 1: {self._representative_figure_text()}",
            f"- 图 2: {self._diagnostic_figure_text()}",
            f"- 图 3: {self._boundary_figure_text()}",
            f"- 图 4: {self._dynamic_figure_text()}",
            "",
            "## 建议论文表",
            "",
            f"- 表 1: {self._table_recommendation_text()}",
            "- 表 2: accepted / failed 边界或 best-worst gap 汇总。",
            "",
            "## 可复现性说明",
            "",
            "- 原始结果以 `summary_metrics.csv`、`index.json` 和 `experiment_manifest.json` 为准。",
            "- 若需展示动态过程，优先引用 `dashboard.html`、`mode_timeline.json` 和 `runtime_schedule.json`。",
            "- 建议在论文或技术报告中同时引用最佳 run、最差 run 和验收统计，避免只展示单次最优截图。",
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def write_runtime_schedule(self, filename: str = "runtime_schedule.json") -> Path:
        path = self.summary.output_dir / filename
        plan = self.summary.plan
        runtime = None if plan is None else plan.runtime
        if runtime is None:
            raise ValueError("experiment plan has no runtime process")
        schedule = runtime.schedule(plan.scenario.time.duration_s)
        payload = {
            "schedule_version": 1,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "satmodel_version": __version__,
            "duration_s": plan.scenario.time.duration_s,
            "runtime": runtime.to_mapping(),
            "event_count": len(schedule),
            "events": schedule,
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        return path

    def write_mode_timeline(self, filename: str = "mode_timeline.json") -> Path:
        path = self.summary.output_dir / filename
        plan = self.summary.plan
        mission = None if plan is None else plan.mission
        if mission is None:
            raise ValueError("experiment plan has no mission sequence")
        payload = {
            "timeline_version": 1,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "satmodel_version": __version__,
            "duration_s": mission.duration_s,
            "mission": mission.to_mapping(),
            "timeline": mission.mode_timeline().to_mapping(),
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        return path

    def write_dashboard(self, filename: str = "dashboard.html") -> Path:
        return build_dashboard(self.summary.output_dir, filename)

    def write_outputs(self) -> dict[str, Path]:
        outputs = {
            "summary_metrics": self.write_metrics_csv(),
            "study_manifest": self.write_study_manifest(),
            "experiment_manifest": self.write_experiment_manifest(),
        }
        if self._has_runtime():
            outputs["runtime_schedule"] = self.write_runtime_schedule()
        if self._has_mission():
            outputs["mode_timeline"] = self.write_mode_timeline()
        outputs["index"] = self.write_index()
        outputs["report"] = self.write_markdown()
        outputs["report_summary"] = self.write_report_summary()
        outputs["paper_summary"] = self.write_paper_summary()
        outputs["dashboard"] = self.write_dashboard()
        return outputs

    def _fieldnames(self) -> list[str]:
        names: list[str] = []
        for row in self.summary.rows:
            for key in row:
                if key not in names:
                    names.append(key)
        return names

    def _has_runtime(self) -> bool:
        return self.summary.plan is not None and self.summary.plan.runtime is not None

    def _has_mission(self) -> bool:
        return self.summary.plan is not None and self.summary.plan.mission is not None

    def _experiment_title(self) -> str:
        if self.summary.plan is not None:
            return self.summary.plan.name
        if self.summary.rows:
            return str(self.summary.rows[0].get("scenario", "satmodel_experiment"))
        return "satmodel_experiment"

    def _research_question_text(self) -> str:
        if self.summary.plan is not None:
            metadata = self.summary.plan.to_mapping().get("metadata", {})
            description = str(metadata.get("description") or "").strip()
            if description:
                return description
        return "当前实验重点是比较不同配置下的姿态误差、控制代价和验收边界。"

    def _core_conclusion_text(self) -> str:
        acceptance = self.acceptance_summary()
        best = self.best_row()
        worst = self.worst_row()
        if acceptance["failed_count"] == 0:
            return (
                f"当前实验整体通过率为 {acceptance['acceptance_rate']:.1%}，"
                f"最佳 run 为 {self._row_value(best, 'run_id')}，"
                f"末端误差约 {self._format_number(self._row_value(best, 'final_error_deg'))} deg，"
                "说明当前方案已具备形成代表结果的基础。"
            )
        return (
            f"当前实验通过率为 {acceptance['acceptance_rate']:.1%}，"
            f"最差 run 为 {self._row_value(worst, 'run_id')}，"
            "建议优先围绕失败边界、最差工况和代表图包继续诊断。"
        )

    def _representative_figure_text(self) -> str:
        return "最佳 / 最差 run 摘要、通过率和边界工况对照图。"

    def _diagnostic_figure_text(self) -> str:
        return "验收失败原因、动态峰值和最差 run 解释图。"

    def _boundary_figure_text(self) -> str:
        if self._has_mission():
            return "任务模式切换、边界工况或主线链路对照图。"
        return "关键 run 对比、accepted-failed boundary 或参数边界图。"

    def _dynamic_figure_text(self) -> str:
        if self._has_runtime() or self._has_mission():
            return "姿态回放关键帧、mode timeline 或 runtime schedule 联动图。"
        return "关键时序图，例如姿态误差和控制力矩动态过程。"

    def _table_recommendation_text(self) -> str:
        params = self.parameter_columns()
        if params:
            return f"参数列 {', '.join(f'`{name}`' for name in params[:4])} 与关键指标的对照汇总表。"
        return "关键指标汇总表，包括 final_error_deg、rms_error_deg 和 peak_torque_nm。"

    def _next_step_text(self) -> str:
        acceptance = self.acceptance_summary()
        if acceptance["failed_count"] > 0:
            return "先回到当前实验链路复查失败边界，再决定是继续整定、扩鲁棒性还是进入任务/执行器实验。"
        return "建议把当前结果沉淀为代表实验，再继续扩到鲁棒性、任务模式或执行器边界主线。"

    @staticmethod
    def _column_list_text(columns: list[str]) -> str:
        if not columns:
            return "无"
        return ", ".join(f"`{column}`" for column in columns)

    @staticmethod
    def _cell(value) -> str | int | float:
        if isinstance(value, (str, int, float)):
            return value
        return json.dumps(value, ensure_ascii=False, default=str)

    @staticmethod
    def _is_number(value) -> bool:
        if isinstance(value, bool):
            return False
        try:
            float(value)
        except (TypeError, ValueError):
            return False
        return True

    @staticmethod
    def _numeric_value(value, *, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _format_number(value) -> str:
        try:
            return f"{float(value):.6g}"
        except (TypeError, ValueError):
            return ""

    @staticmethod
    def _row_value(row: dict[str, Any] | None, key: str):
        if row is None:
            return ""
        return row.get(key, "")
