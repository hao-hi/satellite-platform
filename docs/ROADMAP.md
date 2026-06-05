# satmodel 路线图

当前 `satmodel` 的目标是从轻量姿态控制研究库，逐步演进为配置驱动、可复现实验、可扩展运行时和高保真模型分层清楚的卫星姿态控制仿真平台。后续路线统一参考成熟项目范式：

- Basilisk：process / task / module 分层，强调模块化执行顺序和可测试仿真组件。
- Tudat：environment setup / propagation setup / output 分离，强调环境对象、传播设置和输出变量边界。
- GMAT：资源对象和 mission sequence 分离，强调任务流程、资源配置和用户可读脚本。

当前稳定研究库版本由 Git 标签 `v0.1.0-current` 保留；平台化迭代默认在 `codex/platform-v0.2` 分支推进。

## 路线原则

1. 旧入口保留：`ScenarioRunner`、`SimulationConfig`、`SimulationResult`、`build_default_system()` 和 `build_cubesat_reaction_wheel_system()` 继续可用。
2. 平台能力优先加在编排层：项目、场景、实验计划、运行时、记录器和报告器，不把任务逻辑塞进动力学或控制器。
3. 环境设置和传播设置分离：轨道、地磁、大气、太阳、几何等环境模型不应和积分器、调度器混在同一接口里。
4. 任务序列和物理模型分离：模式切换、参考切换、实验步骤属于 mission/runtime 层，不属于刚体动力学层。
5. 每一阶段都要有可运行示例、结果产物、回归测试和文档，避免只做代码结构而没有实验闭环。

## v0.2：轻量平台骨架

目标是把当前单场景仿真库升级为配置驱动的实验平台雏形。

已经形成的能力：

1. `ScenarioSpec`：描述时间、初始状态、系统构造、控制器、环境、执行机构、传感器、验收和输出。
2. `compile_scenario()`：把配置编译为当前 `SatelliteSystem` 和 `SimulationConfig`。
3. `StudyRunner`：组织单场景、参数扫描和轻量 Monte Carlo seed 序列。
4. `ResultWriter`：生成 run 级 `manifest.json`、`metrics.csv`、`time_history.csv`、`events.csv` 和 `README.md`。
5. study 级结果：`README.md`、`index.json`、`summary_metrics.csv`、`study_manifest.json`。

## v0.3：平台架构收敛

目标是把 v0.2 的配置驱动 runner 收敛为成熟平台式架构，而不是继续堆在单个 runner 文件中。

计划交付：

1. 固化 `satmodel.platform` 为长期主入口，区分 `PlatformProject`、`ExperimentPlan`、`ExperimentRunner`、`ExperimentSummary` 和 `ReportBuilder`。
2. 已将 `satmodel.platform` 拆成 `plan.py`、`runner.py`、`records.py`、`reporting.py` 和 `project.py`，`core.py` 仅作为兼容转发入口。
3. 生成 `experiment_manifest.json`，记录实验计划、场景、扫描/Monte Carlo 设置、结果 schema 和 run 摘要。
4. `StudyRunner` 保留为兼容入口，但长期平台 API 以 `ExperimentRunner` 和 `PlatformProject` 为准。
5. `satmodel-run-scenario` 继续可用，`satmodel-run-experiment` 成为批量实验主入口。

暂不优先做：

- 不把故障注入作为 v0.3 主线。
- 不引入数据库、Web UI、Parquet、HDF5 或 Pydantic。
- 不重写底层刚体动力学、控制器、执行机构和环境模型。

## v0.4：运行时与任务序列

目标是引入成熟仿真平台常见的 runtime / mission sequence 层，让正常任务流程、模式切换和采样周期具有清晰位置。

计划交付：

1. `RuntimeProcess`、`RuntimeTask`、`RuntimeModule`：参考 Basilisk 的 process/task/module 思想，描述模块执行周期、优先级和顺序。
2. `MissionSequence`：参考 GMAT 的 mission sequence 思想，描述实验步骤、参考姿态切换、控制模式切换和运行区间。
3. `ModeTimeline`：支持 detumble、inertial hold、sun pointing、earth pointing、安全模式等模式的时间线表达。
4. 多速率调度：传感器、估计器、控制器、执行机构和记录器可以使用不同更新周期。
5. 结果产物增加 runtime manifest 和 mode timeline，支持后续回放和可视化。

优先级说明：本阶段服务正常任务流程和调度，不把故障注入作为主需求；故障和降额可以作为 mission event 的后续扩展。

## v0.5：高保真建模

目标是按 Tudat/GMAT 风格把环境、传播、姿态、执行机构和传感器模型分层升级，形成任务级验证前的高保真研究基线。

计划交付：

1. Environment setup：更严格的轨道源、绝对时间、地理坐标、太阳方向、地磁和大气模型配置。
2. Propagation setup：积分器后端、状态变量、终止条件和 dependent variables 记录边界。
3. Spacecraft model：面元几何、面元气动、面元 SRP、质量属性组件化和时变惯量扩展。
4. Actuator model：反作用轮摩擦、一阶滞后、安装误差、动量卸载、磁力矩器。
5. Sensor model：星敏感器、太阳敏感器、磁强计和更丰富的陀螺误差模型。
6. Validation cases：用 Monte Carlo 场景和 Basilisk/GMAT/Tudat 风格参考案例进行趋势级验证。

## v0.6：可视化、实验数据库和产品化

目标是把实验平台变成更易用、更适合团队协作和结果复查的工具。

计划交付：

1. 实验目录浏览、指标筛选、run 对比和验收状态检索。
2. 姿态误差、轮速、扰动力矩预算、模式时间线和控制诊断图。
3. 三维姿态回放和 mission timeline 回放。
4. 结果 schema 版本化、迁移策略、变更日志和发布检查。
5. CI、安装后 smoke test、包构建检查和示例实验回归。

## 长期研究方向

1. 在固定步长 RK4 积分器之外，增加 `solve_ivp` 或更通用的传播后端。
2. 增加批量最小二乘、EKF 惯量辨识和完整惯量矩阵可观性诊断。
3. 建立控制器基准测试集，用统一场景比较 PD、LADRC 和后续控制器。
4. 将配置、数据产品和验证基准沉淀为可复现实验模板。
