# satmodel 平台化路线与实施计划

本文档记录 `satmodel` 从轻量姿控仿真库演进到成熟实验平台的实施路线。整体范式参考 Basilisk 的 process/task/module 分层、Tudat 的 environment setup / propagation setup / output 分离，以及 GMAT 的资源与 mission sequence 组织方式。当前 `v0.1` 稳定研究库能力由 `v0.1.0-current` 标签保留。

## 平台目标

平台化优先解决实验组织、复现、结果追踪和运行编排问题，而不是一开始堆叠所有高保真模型。

核心目标：

1. 用配置描述场景和实验计划，减少为每个实验改 Python 脚本的需求。
2. 用 `PlatformProject` 管理工作区、场景、实验计划、结果目录和平台 manifest。
3. 用 `ExperimentPlan` 表达单场景、参数扫描、Monte Carlo 和后续任务序列。
4. 用 `ExperimentRunner` 执行计划并生成标准 run record。
5. 用 `ReportBuilder` 统一生成人读报告和机器可读索引。
6. 保持 `ScenarioRunner.run(SimulationConfig)` 等旧入口可用。

非目标：

1. 不直接照搬 Basilisk、GMAT、Tudat 的源码或复杂依赖。
2. 不把平台编排逻辑塞进动力学、控制器、执行机构或扰动模型。
3. 不把故障注入作为下一阶段优先级；任务流程、模式和调度先服务正常仿真。
4. 不强制引入数据库、Web UI、Parquet、HDF5 或 Pydantic。

## 成熟平台分层

| 层 | 职责 | satmodel 对应 |
| --- | --- | --- |
| Project | 工作区、场景目录、结果目录、manifest | `PlatformProject` |
| Environment Setup | 轨道、地磁、大气、太阳、几何和外部场 | `ScenarioEnvironmentSpec`、`EnvironmentModel` |
| Propagation Setup | 初始状态、积分器、时长、步长、被传播状态 | `ScenarioTimeSpec`、`SimulationConfig`、后续 propagation settings |
| Runtime | process/task/module、调度周期、优先级 | `RuntimeProcess`、`RuntimeTask`、`RuntimeModule` |
| Mission Sequence | 模式切换、参考切换、实验步骤 | `MissionSequence`、`ModeTimeline` |
| Recorder | run record、指标、时序、事件、遥测 | `ExperimentRecord`、`ResultWriter` |
| Report | README、CSV、JSON index、实验 manifest | `ReportBuilder` |

## v0.2：轻量可用平台骨架

已经落地的轻量接口：

| 接口 | 作用 |
| --- | --- |
| `ScenarioSpec` | 配置驱动场景对象，描述时间、初始状态、系统构造、控制器、环境、执行机构、传感器、验收和输出。 |
| `compile_scenario()` | 把 `ScenarioSpec` 编译为现有 `SatelliteSystem` 和 `SimulationConfig`。 |
| `StudyRunner` | 兼容入口，组织单场景、简单参数扫描和轻量 Monte Carlo seed 批量实验。 |
| `ResultWriter` | 将单个 `SimulationResult` 写为 `manifest.json`、`metrics.csv`、`time_history.csv`、`events.csv` 和 `README.md`。 |
| `StudySummary` | 兼容 summary facade，保留 v0.2 调用体验。 |

v0.2 的设计原则仍然有效：标准库优先，JSON/CSV/Markdown 优先，复杂数据格式后置。

## v0.3：平台架构收敛

当前已经开始落地的平台入口：

| 接口 | 长期定位 |
| --- | --- |
| `PlatformProject` | 平台工作区入口，管理场景目录和结果目录。 |
| `ExperimentPlan` | 长期主实验计划对象，描述场景、扫描、Monte Carlo、输出和验收。 |
| `ExperimentRunner` | 长期主实验执行器，生成标准化 run record。 |
| `ExperimentSummary` | 实验摘要对象，提供指标表、验收统计和最佳 run 查询。 |
| `ReportBuilder` | study/experiment 级报告生成器。 |

`satmodel.platform` 已经拆成长期维护所需的聚焦模块，`core.py` 仅作为兼容转发入口：

```text
satmodel/platform/
  plan.py        ExperimentPlan 和加载/校验
  runner.py      ExperimentRunner 和 run case 生成
  records.py     ExperimentRecord 和 ExperimentSummary
  reporting.py   ReportBuilder 和数据产物
  project.py     PlatformProject 和工作区路径规则
```

兼容策略：

1. `StudyRunner` 继续存在，但只作为兼容壳，内部委托 `ExperimentRunner`。
2. `ResultWriter` 只负责 run 级产物；实验级产物由 `ReportBuilder` 负责。
3. `satmodel-run-scenario` 继续可用；批量实验主入口转向 `satmodel-run-experiment`。
4. `ScenarioRunner` 和 `SimulationConfig` 不因平台重构而改变语义。

## v0.4：运行时与任务序列

目标是建立成熟项目常见的 runtime / mission sequence 层。

当前已新增：

1. `RuntimeProcess`：一组 task 的执行容器，可以展开为确定性的事件 schedule。
2. `RuntimeTask`：带更新周期、优先级、开始/停止时间和模块列表的任务。
3. `RuntimeModule`：传感器、估计器、控制器、执行机构、记录器等可调度模块。
4. `MissionSequence`：描述仿真任务步骤、参考切换和模式切换。
5. `ModeTimeline`：可按时间查询 detumble、惯性定向、对日、对地、安全模式等模式区间。
6. `single_rate` runtime 模板：按当前 `ScenarioRunner.step` 顺序生成 environment、disturbance、sensor、estimator、controller、actuator、dynamics、recorder 事件。
7. `single_mode` 和 `detumble_then_hold` mission 模板：用少量字段生成常见任务模式时间线。

当前边界：

1. 新 runtime/mission 类型先作为描述和验证层，不替换 `ScenarioRunner` 的物理执行路径。
2. `ExperimentPlan` 支持可选 runtime 和 mission 字段；旧计划文件无需修改即可继续运行。
3. 当实验计划包含 runtime 或 mission 时，实验根目录会生成 `runtime_schedule.json` 和 `mode_timeline.json`，并在 `index.json` 与 README 中建立索引。
4. 模板简写会自动使用场景 `dt_s` 和 `duration_s`，避免用户重复维护调度步长和任务时长。

本阶段优先服务正常任务流程和多速率调度；故障注入、丢包、降额可以后续作为 mission event 扩展。

## v0.5：高保真建模

目标是把物理模型从轻量研究基线升级为更接近任务分析的工程基线。

计划新增：

1. 环境高保真：IGRF、NRLMSIS、TLE/SGP4、星历太阳方向、半影/本影。
2. 传播高保真：可替换积分器、dependent variables、终止条件和多弧传播预留。
3. 航天器高保真：面元几何、面元气动、面元 SRP、质量属性组件化和时变惯量。
4. 执行机构高保真：反作用轮摩擦、一阶滞后、安装误差、动量卸载和磁力矩器。
5. 传感器高保真：星敏感器、太阳敏感器、磁强计和更完整的陀螺误差模型。
6. 验证基线：Monte Carlo 场景、论文基准和 Basilisk/GMAT/Tudat 风格趋势对比。

## v0.6：可视化、数据库和产品化

当前已新增：

1. `dashboard.html`：实验根目录的中文静态结果浏览界面，支持 run 筛选、验收状态筛选、指标柱状图、run 表、姿态误差动画、姿态误差/角速度/力矩时序图、runtime schedule 和 mode timeline。
2. `satmodel-build-dashboard OUTPUT_DIR`：为已有实验目录补建带仿真结果图的静态界面。
3. `satmodel-platform-ui`：中文本地浏览器控制台，支持发现 `scenarios/` 下的场景和实验计划、查看和校验场景、从场景生成新实验计划、校验计划、运行实验并打开结果 dashboard。

后续计划：

1. 轮速、扰动力矩预算、模式时间线和控制诊断图的更完整对比视图。
2. 更高保真的三维姿态回放和 mission timeline 回放。
3. 实验数据库、结果 schema 版本、迁移策略、变更日志和发布流程。
4. CI、安装后 smoke test、构建检查和示例实验回归。

## 数据产品

每个 run 目录建议至少包含：

| 文件 | 内容 |
| --- | --- |
| `manifest.json` | 场景名、schema 版本、随机种子、satmodel 版本、验收结果和配置摘要。 |
| `metrics.csv` | run 级指标，例如初始误差、末端误差、RMS 误差、控制力矩积分、峰值力矩和验收结果。 |
| `time_history.csv` | 常用时序数据，例如时间、姿态误差、角速度、控制力矩和轮组摘要。 |
| `events.csv` | 稀疏事件日志；当前只记录已有轻量事件，后续接入 mission timeline。 |
| `README.md` | run 级人读报告。 |

每个实验根目录建议至少包含：

| 文件 | 内容 |
| --- | --- |
| `README.md` | 实验摘要，记录 run 数、通过/失败数量、通过率、最佳 run、最差 run、参数列、指标列和文件索引。 |
| `index.json` | 面向平台浏览和可视化的机器可读索引。 |
| `summary_metrics.csv` | 所有 run 的指标、参数列、系统选择、验收结果和输出目录。 |
| `study_manifest.json` | v0.2 兼容 manifest。 |
| `experiment_manifest.json` | 平台实验 manifest，记录实验计划、场景、扫描/Monte Carlo 设置、可选 runtime/mission 描述和 run 摘要。 |
| `runtime_schedule.json` | 可选产物；当计划包含 runtime 时，记录 process/task/module 展开的确定性事件列表。 |
| `mode_timeline.json` | 可选产物；当计划包含 mission 时，记录任务步骤、模式区间和参考切换。 |
| `dashboard.html` | 中文静态结果浏览界面，可直接打开查看指标、run、验收状态、姿态误差动画、仿真时序结果图、runtime schedule 和 mode timeline。 |

## 使用入口

场景入口：

```bash
satmodel-validate-scenario scenarios/quick_pd_zero.json
satmodel-run-scenario scenarios/quick_pd_zero.json --output results/platform/quick_pd_zero
```

实验计划入口：

```bash
satmodel-validate-experiment scenarios/quick_pd_experiment.json
satmodel-run-experiment scenarios/quick_pd_experiment.json --output results/quick_pd_experiment
satmodel-build-dashboard results/quick_pd_experiment
satmodel-platform-ui --open
```

Python 入口：

```python
from satmodel import PlatformProject, ExperimentRunner, load_experiment_plan

plan = load_experiment_plan("scenarios/quick_pd_experiment.json")
summary = ExperimentRunner(plan, output_dir="results/quick_pd_experiment").run()
print(summary.best_row())

project = PlatformProject("workspace")
project.run(plan)
```

兼容入口仍继续可用：

```python
from satmodel import ScenarioRunner, SimulationConfig, build_default_system

system = build_default_system(controller="pd")
result = ScenarioRunner(system).run(SimulationConfig(duration=5.0, dt=0.02))
```

## 测试策略

1. 场景配置解析测试：默认值、非法字段、非法时间设置和系统构造选择。
2. 实验计划测试：内联场景、相对路径场景、参数扫描、Monte Carlo、未知字段拒绝。
3. 兼容测试：`StudyRunner`、`satmodel-run-scenario` 和 `ScenarioRunner.run(SimulationConfig)` 行为不变。
4. 结果写入测试：run 级和 experiment 级产物存在且字段稳定。
5. 回归测试：`python -m pytest -q` 继续通过。

## 实施顺序

建议拆成小提交推进：

1. `Document mature platform roadmap`：统一路线和文档口径。
2. `Modularize platform package`：把当前 `platform.core` 拆为 plan/runner/records/reporting/project。
3. `Add runtime skeleton`：已新增 RuntimeProcess/RuntimeTask/RuntimeModule 只读骨架和文档。
4. `Add mission sequence skeleton`：已新增 MissionSequence/ModeTimeline 的配置和验证。
5. `Connect runtime manifests`：已把 runtime schedule 和 mode timeline 写入实验级结果产物。
6. `Add runtime mission templates`：已新增 `single_rate`、`single_mode` 和 `detumble_then_hold` 模板。
7. `Add high-fidelity model adapters`：按环境、传播、执行机构、传感器逐步扩展。

每一步都应运行测试，并避免把平台架构、高保真物理模型和可视化产品化混在同一个提交里。
