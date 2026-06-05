# satmodel 架构说明

`satmodel` 是一个面向第一版卫星姿态仿真的可组合 Python 库。它的设计目标不是把所有物理过程写进一个大函数，而是把卫星本体、环境、扰动、执行机构、传感器、估计器和控制器拆成可替换组件。

如果需要从入口 API、模型公式、默认参数和资料来源完整理解项目，请先阅读 [项目总说明](PROJECT_GUIDE.md)。

## 公共层次

高层接口包括：

- `SatelliteSystem`：组件装配入口。
- `ScenarioRunner`：单速率固定步长仿真循环。
- `SimulationResult`：保存时间序列、遥测量和常用性能指标。

组件层接口包括：

- `EnvironmentModel.sample(t)`：按仿真时间采样环境上下文。
- `OrbitProvider.state_at(t, epoch_utc)`：按相对时间和绝对 epoch 给出轨道状态。
- `OrbitalEnvironment` 使用的地磁场和大气密度后端。
- `DisturbanceEffectorSet.torques(state, inertia, environment_context)`：计算具名扰动力矩预算。
- `SpacecraftDynamics.step(state, torque, disturbance, dt)`：传播刚体姿态和附加状态。
- `SensorSuite.measure(state, environment_context, t)`：生成姿态和陀螺测量。
- 理想执行器 `TorqueActuator.apply(command, dt)`。
- 耦合反作用轮执行器 `ReactionWheelStateEffector.apply(command, dt)`。
- 估计器对象的 `update(measurement, applied_torque, dt)`。
- 控制器对象的 `command(reference, estimate, dt)`。
- 优化器对象的 `optimize(objective, bounds)`。

## 数据流

每一个仿真步大致执行以下流程：

1. `ScenarioRunner` 从当前 `RigidBodyState` 读取姿态、角速度和时间。
2. 环境层采样 `EnvironmentContext`，包括轨道位置、速度、地磁场、大气密度、太阳方向、地影、绝对时刻和地理位置。
3. 具名扰动效应器根据卫星状态、惯量和环境上下文计算 `TorqueBudget`。
4. 传感器层生成姿态测量和陀螺测量。
5. 估计器根据测量值和上一拍实际执行力矩更新估计状态。
6. 控制器根据参考姿态和估计状态输出本体系力矩命令。
7. 执行器把命令转换为实际本体系力矩。
8. 动力学层使用实际控制力矩、扰动力矩和可选轮组内部状态传播下一步刚体状态。
9. `SimulationResult` 记录真值、估计、测量、力矩预算、控制器诊断和执行机构遥测。

## 平台化目标架构

`v0.2` 之后的平台化方向是在现有模型库外侧增加编排层，而不是重写底层物理组件。现有 `ScenarioRunner`、`SimulationConfig` 和 `SimulationResult` 应继续作为稳定单场景入口；新增平台入口负责把配置、实验和结果组织成可复现工作流。

平台层统一参考成熟项目的分层口径：

| 平台层 | 借鉴范式 | satmodel 边界 |
| --- | --- | --- |
| Project | GMAT 资源管理、Tudat settings 容器 | 工作区、场景目录、结果目录和平台 manifest。 |
| Environment Setup | Tudat `SystemOfBodies` 和环境模型设置 | 轨道、地磁、大气、太阳、几何和外部场。 |
| Propagation Setup | Tudat propagator settings | 初始状态、传播时长、步长、积分器、被记录变量。 |
| Runtime | Basilisk process/task/module | 后续多速率调度、任务优先级和模块执行顺序。 |
| Mission Sequence | GMAT mission control sequence | 后续模式切换、参考切换和任务步骤。 |
| Recorder | Basilisk/Tudat 输出变量 | run record、指标、时序、事件和遥测。 |
| Report | 平台后处理层 | Markdown/CSV/JSON 报告、索引和实验 manifest。 |

建议的第一阶段架构流为：

```text
ScenarioSpec
    -> ScenarioCompiler
    -> ScenarioRunner / StudyRunner
    -> ResultWriter
    -> Markdown/CSV/JSON Report
```

各层职责：

- `ScenarioSpec`：描述场景意图，例如时间设置、初始状态、系统构造、控制器和输出设置。
- `ScenarioCompiler`：把配置对象转换为当前库已经支持的 `SatelliteSystem` 和 `SimulationConfig`。
- `ScenarioRunner`：继续执行单速率固定步长仿真，保持旧 API 语义不变。
- `StudyRunner`：组织单场景、参数扫描和 Monte Carlo 等批量实验。
- `ResultWriter`：把内存中的 `SimulationResult` 写成可复现实验产物。

`v0.2` 默认采用轻量实现：优先使用标准库数据结构、JSON、CSV 和 Markdown。Pydantic、Parquet、HDF5、数据库和 Web 服务可作为后续增强，不作为第一阶段的硬依赖。

`v0.3` 已经把平台编排提升为独立 `satmodel.platform` 层：

```text
PlatformProject
    -> ExperimentPlan
    -> ExperimentRunner
    -> ScenarioCompiler / ScenarioRunner
    -> ResultWriter
    -> ReportBuilder
    -> Experiment Manifest / Index / Report
```

各层职责：

- `PlatformProject`：管理平台工作区、默认场景目录和结果目录。
- `ExperimentPlan`：描述实验计划，包括单场景、参数扫描、Monte Carlo 和输出根目录。
- `ExperimentRunner`：执行计划，生成标准化 run record。
- `ReportBuilder`：生成 `experiment_manifest.json`、`index.json`、`summary_metrics.csv` 和 `README.md`。
- `StudyRunner`：保留为兼容壳，内部委托给 `ExperimentRunner`。

`satmodel.platform` 已按成熟项目的维护方式拆成聚焦模块，`core.py` 仅保留为兼容转发入口：

```text
satmodel/platform/
  plan.py
  runner.py
  records.py
  reporting.py
  project.py
```

当前职责边界是：`ExperimentPlan` 负责配置契约，`ExperimentRunner` 负责执行，`ExperimentRecord`/`ExperimentSummary` 负责运行结果，`ReportBuilder` 负责数据产品，`PlatformProject` 负责工作区路径和资源定位。

v0.4 已新增轻量 runtime / mission 描述层。它目前作为可验证的调度语义骨架存在，后续可以在这个平台流上替换当前单速率执行器：

```text
ScenarioSpec
    -> ScenarioCompiler
    -> RuntimeProcess / RuntimeTask / RuntimeModule
    -> MissionSequence / ModeTimeline
    -> Recorder / ResultWriter
    -> Report / Replay
```

`RuntimeProcess.schedule(duration_s)` 会先把 process/task/module 展开为确定性的事件列表。`single_rate` runtime 模板按当前 `ScenarioRunner.step` 顺序生成 environment、disturbance、sensor、estimator、controller、actuator、dynamics 和 recorder 事件。多速率调度器应以当前单步数据流作为语义基线；等频配置下，动力学、传感器、估计器、控制器、执行器和记录顺序应保持可解释的一致性。

`ExperimentPlan` 已可选携带 runtime 和 mission 描述。当前 runner 不用它们替换物理执行路径，但会把它们写入 `experiment_manifest.json`，并额外生成 `runtime_schedule.json` 和 `mode_timeline.json`，供后续报告、回放和可视化读取。

`dashboard.html` 是当前轻量可视化入口。它不需要数据库或 Web 服务，直接读取同目录的 `index.json`、`summary_metrics.csv`、`runtime_schedule.json` 和 `mode_timeline.json`，用于浏览 run、指标、验收状态、调度和模式时间线。

`satmodel-platform-ui` 是当前轻量操作入口。它使用本地 HTTP 服务包装平台 API，发现 `scenarios/` 下的实验计划，并通过 `ExperimentRunner` 执行校验和运行；生成的结果仍然落在标准实验目录中，由 `dashboard.html` 负责展示。

任务模式和调度优先服务正常任务流程，例如 detumble、惯性定向、对日、对地和安全模式。故障注入、丢包和降额属于 mission event 的后续扩展，不作为下一阶段主线。

## 两条默认系统路径

项目保留两条轻量系统构造路径：

- `build_default_system()`：使用原有饱和体轴力矩执行器，适合快速验证控制器、估计器和辨识器。
- `build_cubesat_reaction_wheel_system()`：使用刚体 1U CubeSat 质量属性和四轮金字塔反作用轮状态效应器，同时保持控制器面对的仍是本体系力矩命令。

两条路径当前共用默认演示环境：

- 圆轨道 LEO 轨道源、中心偶极地磁场和指数大气。
- 重力梯度、残余磁、气动和太阳光压四类具名扰动力矩。
- 标量在前四元数刚体姿态动力学。
- 简化姿态传感器和陀螺。
- MEKF 姿态估计。
- 可选对角惯量 RLS 辨识。
- PD 或 LADRC 姿态控制。

## 诊断量边界

环境扰动重构和 LADRC 扩张状态观测器中的扰动估计是两类不同诊断量：

- 环境扰动重构是物理残差估计，主要给惯量辨识器和结果分析使用。
- LADRC 的扰动估计是控制器内部的等效输入补偿量。

两者不应直接混用，也不应简单认为数值必须相等。

## 环境扩展口

公共环境层还暴露：

- `KeplerianOrbitProvider`
- `EphemerisOrbitProvider`
- 可选 `TLEOrbitProvider`
- 可选 `IGRFMagneticField`
- 可选 `NRLMSISAtmosphere`

IGRF、NRLMSIS 和 TLE/SGP4 都需要绝对时间和地理位置边界，因此 `OrbitalEnvironment` 负责统一采样时间、轨道状态和坐标转换，再把结果交给扰动效应器计算卫星响应。

## 反作用轮遥测

反作用轮遥测保存在 `SimulationResult.actuator_telemetry` 中，并通过便捷属性暴露：

- `wheel_speeds_rad_s`
- `wheel_torques_nm`
- `wheel_torque_commands_nm`
- `wheel_momentum_nms`
- `wheel_momentum_capacity_nms`
- `wheel_allocation_error_nm`
- `wheel_saturation_flags`

CubeSat 反作用轮路径会把轮速作为内部状态与刚体一起传播，并在刚体陀螺耦合项中包含轮组动量。
