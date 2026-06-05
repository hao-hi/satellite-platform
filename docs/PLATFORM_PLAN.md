# satmodel v0.2 平台化实施计划

本文档记录 `satmodel` 从轻量姿控仿真库演进到配置驱动实验平台的实施路线。当前 `v0.1` 稳定研究库能力由 `v0.1.0-current` 标签保留；`v0.2` 的目标是在不破坏旧 API 的前提下补出轻量平台骨架。

## 目标

`v0.2` 先解决编排和复现问题，而不是优先堆叠高保真模型。

核心目标：

1. 通过配置描述仿真场景，减少用户为每个实验改 Python 脚本的需求。
2. 通过通用实验运行器组织单场景、参数扫描和轻量 Monte Carlo seed 批量实验。
3. 通过标准化结果目录保存指标、时序、配置摘要和人读报告。
4. 保持现有 `ScenarioRunner`、`SimulationConfig`、`SimulationResult` 和示例脚本可用。

非目标：

1. `v0.2` 不重写动力学、控制器、估计器和扰动模型。
2. `v0.2` 不强制引入 Pydantic、Parquet、HDF5、数据库或 Web 服务。
3. `v0.2` 不把多速率调度、任务模式和高保真传感器作为首批必须交付。

## 阶段交付物

### v0.2：轻量可用平台骨架

已经落地的第一批轻量接口：

| 接口 | 作用 |
| --- | --- |
| `ScenarioSpec` | 配置驱动场景对象，描述时间、初始状态、系统构造、控制器和输出。 |
| `compile_scenario()` | 把 `ScenarioSpec` 编译为现有 `SatelliteSystem` 和 `SimulationConfig`。 |
| `StudyRunner` | 组织单场景运行、简单参数扫描和轻量 Monte Carlo seed 批量实验。 |
| `ResultWriter` | 将单个 `SimulationResult` 写为 `manifest.json`、`metrics.csv`、`time_history.csv`、`events.csv` 和 `README.md`。 |
| `StudySummary` | 在实验根目录写出 `summary_metrics.csv` 和 `study_manifest.json`。 |

推荐实现策略：

1. 标准库优先，先用 dataclass、`json`、`csv` 和 `pathlib`。
2. 配置格式可以先支持 JSON；YAML 支持作为可选增强，避免第一阶段增加依赖压力。
3. `StudyRunner` 先覆盖单场景、简单参数扫描和固定 seed 序列 Monte Carlo；更复杂的随机分布采样后续再扩展。
4. `ResultWriter` 先写小规模 CSV 和 Markdown 报告，后续再扩展 Parquet、HDF5 或 HTML。

### v0.3：调度、任务模式和故障注入

计划新增：

1. 多速率调度器，让动力学、传感器、估计器、控制器和日志记录可使用不同周期。
2. 模式管理器，支持 detumble、惯性定向、对日、对地、安全模式和大角度机动。
3. 事件系统，支持故障注入、模式切换、传感器丢包和执行器降额。
4. 事件日志产物，用于故障复现和回放。

### v0.4：高保真模型

计划新增：

1. 面元几何、面元气动和面元太阳光压。
2. 反作用轮摩擦、一阶滞后、安装误差和动量卸载。
3. 磁力矩器、星敏感器、太阳敏感器、磁强计和更丰富的陀螺误差模型。
4. IGRF、NRLMSIS、TLE/SGP4 和星历环境 provider 的参考验证场景。

### v0.5：可视化和发布

计划新增：

1. 姿态误差、轮速、扰动力矩预算和模式时间线报告图。
2. 三维姿态回放和实验目录浏览。
3. 包构建检查、安装后 smoke test、CI、变更日志和版本策略。
4. 结果 schema 版本和迁移策略。

## 建议的数据产品

`v0.2` 每个 run 目录建议至少包含：

| 文件 | 内容 |
| --- | --- |
| `manifest.json` | 场景名、schema 版本、随机种子、satmodel 版本、运行时间、验收结果和配置摘要。 |
| `metrics.csv` | run 级指标，例如初始误差、末端误差、RMS 误差、控制力矩积分、峰值力矩和验收结果。 |
| `time_history.csv` | 常用时序数据，例如时间、姿态误差、角速度、控制力矩和轮组摘要。 |
| `events.csv` | 稀疏事件日志；当前记录起始反作用轮故障，后续扩展时序事件。 |
| `README.md` | 自动生成报告，记录场景、参数、指标表和主要解释。 |

每个实验根目录建议至少包含：

| 文件 | 内容 |
| --- | --- |
| `README.md` | study 级人读摘要，记录 run 数、通过/失败数量、通过率、最佳 run、最差 run、参数列、指标列和文件索引。 |
| `index.json` | 面向后续平台浏览和可视化的机器可读索引，记录验收统计、最佳 run、参数列、指标列和所有 run 摘要。 |
| `summary_metrics.csv` | 所有 run 的指标、参数列、系统选择、故障数量、验收结果和输出目录。 |
| `study_manifest.json` | study 级版本、生成时间、run 数和摘要行。 |

后续增强：

- `events.csv` 或 `events.parquet`：稀疏事件日志。
- `telemetry.h5`：高维稠密遥测和回放数据。
- `report.html`：交互式或更精致的人读报告。

## 接口草案

轻量场景配置示例：

```yaml
schema_version: 1
metadata:
  name: cubesat_rw_pd_demo
  description: 1U CubeSat 四反作用轮 PD 闭环演示

time:
  duration_s: 20.0
  dt_s: 0.02
  seed: 42

system:
  builder: cubesat_reaction_wheel
  controller: pd
  identify_inertia: false
  environment: orbital

controller:
  pd_kp: 0.05
  pd_kd: 0.02

sensors:
  attitude:
    noise_std_rad: 0.0006
  gyro:
    noise_std_rad_s: 0.001
    bias_std_rad_s: 0.002
    bias_rw_scale: 0.02

environment:
  epoch_utc: "2026-01-01T00:00:00Z"
  sun_vector_eci: [1.0, 0.2, 0.1]
  orbit:
    provider: keplerian
    semi_major_axis_m: 6878137.0
    eccentricity: 0.001
    inclination_deg: 97.6
    raan_deg: 15.0

actuators:
  reaction_wheels:
    layout: pyramid_4wheel
    max_torque_nm: 0.007
    initial_speeds_rad_s: [0.0, 0.0, 0.0, 0.0]
    allocation: bounded_pinv

faults:
  - target: reaction_wheel
    action: disable
    index: 0
    when_s: 0.0

acceptance:
  max_final_error_deg: 5.0
  max_rms_error_deg: 20.0
  max_peak_torque_nm: 0.2

initial_state:
  use_default: true

outputs:
  root: results/cubesat_rw_pd_demo
  save_metrics_csv: true
  save_time_history_csv: true
  save_markdown_report: true
```

Python 使用方式：

```python
from satmodel.config import load_scenario
from satmodel.studies import StudyRunner

scenario = load_scenario("scenarios/cubesat_rw_pd_demo.yaml")
summary = StudyRunner(scenario).run()
print(summary.metrics_table())
```

命令行使用方式：

```bash
satmodel-run-scenario scenarios/cubesat_rw_pd_demo.json --output results/cubesat_rw_pd_demo
```

配置验证方式：

```bash
satmodel-validate-scenario scenarios/cubesat_rw_pd_demo.json
```

CLI 也支持覆盖和参数扫描：

```bash
satmodel-run-scenario scenarios/cubesat_rw_pd_demo.json \
  --output results/pd_sweep \
  --set time.seed=9 \
  --sweep controller.pd_kp=0.05,0.08,0.12
```

轻量 Monte Carlo 采用固定 seed 序列，适合先做噪声鲁棒性和批量回归：

```bash
satmodel-run-scenario scenarios/cubesat_rw_pd_demo.json \
  --output results/pd_monte_carlo \
  --monte-carlo 20 \
  --monte-carlo-seed 100
```

批量实验完成后，优先查看输出根目录的 `README.md`，它会汇总通过/失败数量、通过率、最佳 run、最差 run 和关键指标表；后续可视化或自动筛选可以读取同目录的 `index.json`。

平台入口已经支持单场景运行、Python/CLI 简单参数扫描、轻量 Monte Carlo、控制器参数、轻量轨道环境、反作用轮配置和 `when_s=0.0` 起始轮失效。旧入口仍继续可用：

```python
from satmodel import ScenarioRunner, SimulationConfig, build_default_system

system = build_default_system(controller="pd")
result = ScenarioRunner(system).run(SimulationConfig(duration=5.0, dt=0.02))
```

## 测试策略

`v0.2` 代码实现阶段应增加以下测试：

1. 场景配置解析测试：默认值、非法字段、非法时间设置和系统构造选择。
2. 编译测试：`ScenarioSpec` 能生成可运行的 `SatelliteSystem` 和 `SimulationConfig`。
3. 兼容测试：`ScenarioRunner.run(SimulationConfig)` 旧路径行为不变。
4. 结果写入测试：`manifest.json`、`metrics.csv`、`time_history.csv` 和 `README.md` 存在且字段稳定。
5. 实验运行测试：固定 seed 的单场景、参数扫描和 Monte Carlo 批量实验能生成可复现指标。
6. 回归测试：现有示例脚本和 `python -m pytest -q` 继续通过。

## 实施顺序

建议拆成小提交推进：

1. `Add lightweight scenario spec`：新增配置对象和最小加载器。
2. `Add scenario compiler`：把配置编译到现有系统构造器和 `SimulationConfig`。
3. `Add result writer`：生成 manifest、metrics、time history 和 Markdown 报告。
4. `Add study runner`：组织单场景和简单参数扫描。
5. `Document platform workflow`：补充 README、示例和 API 文档。

每一步都应运行测试，并避免把平台化实现与高保真物理模型改造混在同一个提交里。
