# satmodel

**satmodel** 是一个面向小卫星姿态控制研究的轻量 Python 仿真库。项目名称来自 **satellite model**，可以理解为“卫星姿控仿真模型库”。

它把卫星姿态仿真拆成可组合模块：

```text
轨道环境 -> 扰动力矩 -> 传感器 -> 姿态估计 -> 控制器 -> 执行机构 -> 刚体动力学
```

项目适合用于 CubeSat/小卫星 ADCS 的第一版建模、控制律验证、反作用轮仿真、姿态估计、惯量辨识和论文式数值实验。

## 项目特点

- 标量在前四元数姿态表示。
- 固定步长 RK4 刚体姿态传播。
- 圆轨道、Kepler 轨道、星历轨道和可选 TLE/SGP4 轨道源。
- 简化 LEO 环境：地磁场、大气密度、太阳方向和地影。
- 可选 IGRF 地磁场和 NRLMSIS 大气模型适配器。
- 重力梯度、残余磁、气动、太阳光压扰动力矩。
- 理想体轴力矩执行器。
- 三轮正交和四轮金字塔反作用轮执行器。
- 反作用轮力矩/速度限幅、失效降级和 null-space 动量管理。
- 简化姿态传感器和陀螺模型。
- MEKF 姿态估计。
- 可选对角惯量 RLS 辨识。
- PD 和 LADRC 姿态控制器。
- 网格搜索、随机搜索、Nelder-Mead、模拟退火和 PSO 调参工具。
- 可复现的反作用轮阵列研究实验。

## 代码结构

项目采用标准 `src/` layout。第一次阅读代码时，建议先看 `examples/` 里的脚本，再进入 `src/satmodel/system.py` 理解主仿真循环。

```text
satellite-attitude-control-model/
  README.md                         GitHub 首页说明
  pyproject.toml                    Python 包元数据、依赖和命令行入口
  docs/                             架构、物理模型、参考资料和新手导览
  examples/                         可直接运行的演示脚本
  scenarios/                        平台化 JSON 场景模板，可由 CLI 直接验证和运行
  tests/                            单元测试、物理验证和示例 smoke test
  src/satmodel/                     核心 Python 包
```

核心包 `src/satmodel/`：

| 文件或目录 | 作用 |
| --- | --- |
| `__init__.py` | 顶层公共 API 汇总，例如 `ScenarioRunner`、`SimulationConfig`、`build_default_system()`。 |
| `_version.py` | 包版本号。 |
| `_validation.py` | 输入校验工具，例如三维向量、三阶矩阵、单位向量和 UTC 时间。 |
| `types.py` | 核心数据对象，包括刚体状态、参考姿态、轨道状态、环境上下文、力矩预算、传感器测量、估计状态、仿真配置、仿真结果和反作用轮遥测。 |
| `math.py` | 四元数、姿态误差、角速度矩阵、方向余弦矩阵和本体系/惯性系旋转工具。 |
| `geometry.py` | 盒体几何和投影面积计算，供气动和太阳光压模型共用。 |
| `physics.py` | 质量属性、均匀盒体惯量、CubeSat 演示物理配置和反作用轮默认配置入口。 |
| `environment.py` | 轨道源、地理坐标转换、地磁场、大气密度、太阳方向、地影和组合式环境采样。 |
| `disturbances.py` | 重力梯度、残余磁、气动、太阳光压扰动力矩，以及具名扰动力矩预算。 |
| `actuators.py` | 理想力矩执行器、单个反作用轮、反作用轮阵列、力矩分配、限幅、失效和遥测。 |
| `dynamics.py` | 刚体姿态动力学、RK4 积分，以及反作用轮角动量和轮速状态耦合。 |
| `sensors.py` | 简化姿态传感器和陀螺模型，支持噪声、偏置和随机种子复现。 |
| `estimation.py` | MEKF 姿态估计和估计器组合。 |
| `identification.py` | 角加速度辅助、扰动重构、对角惯量 RLS 和辨识诊断量。 |
| `controllers.py` | PD 控制器和三轴 LADRC 控制器。 |
| `optimization.py` | 网格搜索、随机搜索、Nelder-Mead、模拟退火和 PSO 参数优化工具。 |
| `plotting.py` | 仿真结果绘图辅助。 |
| `system.py` | 高层系统装配、默认系统构造器和单速率固定步长仿真循环，是理解项目运行方式的关键文件。 |
| `platform/` | v0.3 平台架构层，包含实验计划、实验运行器、项目工作区和报告构建器。 |
| `studies/` | 可复现实验入口，目前包含反作用轮阵列研究实验。 |

示例脚本 `examples/`：

| 脚本 | 作用 |
| --- | --- |
| `open_loop.py` | 开环刚体传播，不启用控制器。 |
| `pd_closed_loop.py` | PD 姿态稳定闭环，最适合入门。 |
| `ladrc_closed_loop.py` | LADRC 姿态控制和扰动诊断。 |
| `mekf_rls_identification.py` | MEKF 姿态估计和 RLS 惯量辨识。 |
| `tune_pd.py` | 使用 PSO 优化 PD 控制器参数。 |
| `cubesat_reaction_wheels_pd.py` | 1U CubeSat 四反作用轮 PD 闭环。 |
| `cubesat_wheel_failure.py` | 禁用一个反作用轮后的失效降级场景。 |
| `academic_reaction_wheel_study.py` | 反作用轮阵列论文式批量实验。 |

文档 `docs/`：

| 文档 | 作用 |
| --- | --- |
| `NEWCOMER_GUIDE.md` | 面向第一次打开项目的人，解释项目是什么、各文件做什么、怎么运行。 |
| `PROJECT_GUIDE.md` | 项目总说明，集中介绍代码结构、仿真流程、物理公式和默认参数。 |
| `ARCHITECTURE.md` | 架构说明，解释组件层次和单步数据流。 |
| `ROADMAP.md` | 按成熟平台范式整理的 v0.2-v0.6 演进路线。 |
| `PLATFORM_PLAN.md` | 平台化路线与实施计划，说明 Project、Experiment、Runtime、Report 等分层。 |
| `REFERENCES.md` | 参考框架、论文、官方模型和开源项目索引。 |
| `physics/` | 分专题物理模型说明，包括刚体、环境扰动、反作用轮和参数追溯。 |

## 快速安装

要求：

- Python 3.10 或更高版本。
- 如果从 GitHub 克隆或直接安装，需要本机已安装 Git。

只想在其他电脑上安装并调用库：

```bash
python -m pip install git+https://github.com/hao-hi/satellite-attitude-control-model.git
```

安装后验证：

```bash
python -c "import satmodel; print(satmodel.__version__)"
```

如果要运行示例、修改代码或运行测试，推荐克隆后开发安装：

```bash
git clone https://github.com/hao-hi/satellite-attitude-control-model.git
cd satellite-attitude-control-model
python -m pip install -e .
```

注意：`python -m pip install -e .` 必须在包含 `pyproject.toml` 的项目根目录执行。不要在微信文件夹、压缩包内部子目录或其他任意目录执行，否则 pip 会提示找不到 `setup.py` 或 `pyproject.toml`。

## 可选依赖

开发、测试和构建工具：

```bash
python -m pip install -e ".[dev]"
```

绘图能力：

```bash
python -m pip install -e ".[plot]"
```

可选地球环境模型和 TLE/SGP4 适配器：

```bash
python -m pip install -e ".[earth,tle]"
```

常用开发组合：

```bash
python -m pip install -e ".[dev,plot,earth,tle]"
```

说明：

- 基础依赖主要是 `numpy` 和 `matplotlib`。
- `earth` 会安装 `ppigrf` 和 `pymsis`，用于 IGRF 和 NRLMSIS。
- `tle` 会安装 `sgp4`，用于 `TLEOrbitProvider`。

## 最小示例

运行一个默认 PD 闭环姿态控制仿真：

```python
from satmodel import ScenarioRunner, SimulationConfig, build_default_system

system = build_default_system(controller="pd", identify_inertia=True)
config = SimulationConfig(duration=5.0, dt=0.02)

result = ScenarioRunner(system).run(config)

print(result.metrics(config.reference))
```

输出指标包括：

- 初始姿态误差。
- 末端姿态误差。
- RMS 姿态误差。
- 控制力矩积分。
- 峰值执行力矩。

## 轻量平台入口

平台化入口保留原有 `ScenarioRunner` 用法，同时增加配置驱动的场景和实验运行器。下面的例子会生成 run 级 `manifest.json`、`metrics.csv`、`time_history.csv`、`events.csv` 和 `README.md`，并在实验根目录生成 `README.md`、`index.json`、`summary_metrics.csv`、`study_manifest.json` 与 `experiment_manifest.json`：

```python
from satmodel import ScenarioSpec, StudyRunner

scenario = ScenarioSpec(
    metadata={"name": "quick_platform_demo"},
    time={"duration_s": 2.0, "dt_s": 0.02, "seed": 3},
    system={"builder": "default", "controller": "pd", "environment": "zero"},
    controller={"pd_kp": 1.5, "pd_kd": 0.35},
    outputs={"root": "results/quick_platform_demo"},
)

summary = StudyRunner(scenario).run()
print(summary.metrics_table()[0]["final_error_deg"])
```

如果要从文件加载场景，可以使用 `satmodel.load_scenario("scenario.json")`。JSON 是默认无额外依赖路径；YAML 文件在安装 `PyYAML` 后可选支持。

仓库内置了两个可直接运行的 JSON 场景模板：

| 场景 | 作用 |
| --- | --- |
| `scenarios/quick_pd_zero.json` | 短时长 PD 闭环 smoke 场景，使用 zero environment。 |
| `scenarios/cubesat_rw_fault.json` | 1U CubeSat 四轮构型，含 t=0 反作用轮失效。 |

安装为包后可以先验证场景文件，不运行仿真也不写结果：

```bash
satmodel-validate-scenario scenarios/quick_pd_zero.json
```

也可以直接用命令行运行场景文件：

```bash
satmodel-run-scenario scenarios/quick_pd_zero.json --output results/my_run
```

命令行也支持临时覆盖字段和笛卡尔参数扫描：

```bash
satmodel-run-scenario scenarios/quick_pd_zero.json \
  --output results/pd_sweep \
  --set time.seed=9 \
  --sweep controller.pd_kp=0.05,0.08,0.12
```

参数扫描会在输出根目录生成 `README.md`、`index.json`、`summary_metrics.csv` 和 `study_manifest.json`，每个 run 则放在 `run_000/`、`run_001/` 等子目录中。

如果要做随机噪声鲁棒性检查，可以用 Monte Carlo seed 序列批量运行：

```bash
satmodel-run-scenario scenarios/quick_pd_zero.json \
  --output results/pd_monte_carlo \
  --monte-carlo 20 \
  --monte-carlo-seed 100
```

这会生成 `time.seed=100..119` 的 20 个 run。也可以和 `--sweep` 组合，形成“每组参数下跑多组随机 seed”的小型批量实验。跑完后先打开输出根目录的 `README.md` 看通过/失败数量、最佳 run 和关键指标；程序化分析则读取 `index.json` 或 `summary_metrics.csv`。

`v0.3` 新增了实验计划入口，适合把一个完整实验保存成可复用 JSON。`v0.4` 起，实验计划可以可选包含 runtime 和 mission 描述，运行后额外生成 `runtime_schedule.json` 与 `mode_timeline.json`：

```bash
satmodel-validate-experiment scenarios/quick_pd_experiment.json
satmodel-run-experiment scenarios/quick_pd_experiment.json --output results/quick_pd_experiment
```

实验计划会生成 `experiment_manifest.json`，记录计划元数据、场景、扫描/Monte Carlo 设置、可选 runtime/mission 描述和所有 run 摘要。旧的 `satmodel-run-scenario` 和 Python `StudyRunner` 仍可用，它们内部会委托到新的平台层。

后续平台路线不在 README 展开维护，统一见 [路线图](docs/ROADMAP.md) 和 [平台化路线](docs/PLATFORM_PLAN.md)。

场景文件也可以配置 orbital 环境和轨道参数：

```json
{
  "schema_version": 1,
  "metadata": {"name": "keplerian_platform_demo"},
  "time": {"duration_s": 20.0, "dt_s": 0.02, "seed": 42},
  "system": {"builder": "default", "controller": "pd", "environment": "orbital"},
  "controller": {"pd_kp": 1.5, "pd_kd": 0.35},
  "sensors": {
    "attitude": {"noise_std_rad": 0.0006},
    "gyro": {
      "noise_std_rad_s": 0.001,
      "bias_std_rad_s": 0.002,
      "bias_rw_scale": 0.02
    }
  },
  "environment": {
    "epoch_utc": "2026-01-01T00:00:00Z",
    "sun_vector_eci": [1.0, 0.2, 0.1],
    "orbit": {
      "provider": "keplerian",
      "semi_major_axis_m": 6878137.0,
      "eccentricity": 0.001,
      "inclination_deg": 97.6,
      "raan_deg": 15.0
    }
  },
  "actuators": {
    "reaction_wheels": {
      "layout": "pyramid_4wheel",
      "max_torque_nm": 0.007,
      "initial_speeds_rad_s": [0.0, 0.0, 0.0, 0.0],
      "allocation": "bounded_pinv"
    }
  },
  "faults": [
    {"target": "reaction_wheel", "action": "disable", "index": 0, "when_s": 0.0}
  ],
  "acceptance": {
    "max_final_error_deg": 5.0,
    "max_rms_error_deg": 20.0,
    "max_peak_torque_nm": 0.2
  },
  "outputs": {"root": "results/keplerian_platform_demo"}
}
```

## 运行示例脚本

进入项目根目录后，可以直接运行：

```bash
python examples/open_loop.py
python examples/pd_closed_loop.py
python examples/ladrc_closed_loop.py
python examples/mekf_rls_identification.py
python examples/tune_pd.py
python examples/cubesat_reaction_wheels_pd.py
python examples/cubesat_wheel_failure.py
```

部分示例支持绘图：

```bash
python examples/pd_closed_loop.py --plot
python examples/cubesat_reaction_wheels_pd.py --plot
```

示例作用：

| 脚本 | 作用 |
| --- | --- |
| `examples/open_loop.py` | 开环刚体传播，不启用控制器。 |
| `examples/pd_closed_loop.py` | PD 姿态稳定闭环，最适合入门。 |
| `examples/ladrc_closed_loop.py` | LADRC 姿态控制和扰动诊断。 |
| `examples/mekf_rls_identification.py` | MEKF 姿态估计和 RLS 惯量辨识。 |
| `examples/tune_pd.py` | 使用 PSO 优化 PD 控制器参数。 |
| `examples/cubesat_reaction_wheels_pd.py` | 1U CubeSat 四反作用轮 PD 闭环。 |
| `examples/cubesat_wheel_failure.py` | 禁用一个反作用轮后的失效降级场景。 |
| `examples/academic_reaction_wheel_study.py` | 反作用轮阵列论文式批量实验。 |

## 反作用轮仿真

CubeSat 反作用轮系统可以这样调用：

```python
from satmodel import ScenarioRunner, SimulationConfig, build_cubesat_reaction_wheel_system

system = build_cubesat_reaction_wheel_system(controller="pd")
config = SimulationConfig(duration=6.0, dt=0.02)

result = ScenarioRunner(system).run(config)

print(result.metrics(config.reference))
print(result.wheel_speeds_rad_s.shape)
print(result.wheel_allocation_error_nm.max())
```

反作用轮不是简单的力矩限幅器。它在项目中作为带内部状态的 `ReactionWheelStateEffector` 接入动力学：

```text
控制器三轴力矩命令
    -> 轮组分配器
    -> 单轮力矩/速度限幅
    -> 本体反作用力矩
    -> 姿态、角速度和轮速一起积分
```

仿真结果会记录：

- `result.wheel_speeds_rad_s`
- `result.wheel_torques_nm`
- `result.wheel_torque_commands_nm`
- `result.wheel_momentum_nms`
- `result.wheel_momentum_capacity_nms`
- `result.wheel_allocation_error_nm`
- `result.wheel_saturation_flags`

单轮失效示例：

```python
system = build_cubesat_reaction_wheel_system(controller="pd")
system.actuator.disable_wheel(0)
```

## 可选高保真环境

安装 `earth` 和 `tle` 可选依赖后，可以使用 TLE/SGP4、IGRF 和 NRLMSIS：

```python
from datetime import datetime, timezone

from satmodel import (
    EnvironmentConfig,
    IGRFMagneticField,
    NRLMSISAtmosphere,
    OrbitalEnvironment,
    SpaceWeatherInputs,
    TLEOrbitProvider,
)

line1 = "1 25544U 98067A   26142.51965852  .00007327  00000+0  13945-3 0  9999"
line2 = "2 25544  51.6330  67.1668 0007537  86.3178 273.8672 15.49313075567774"

environment = OrbitalEnvironment(
    EnvironmentConfig(datetime(2026, 5, 22, 12, 28, 18, tzinfo=timezone.utc)),
    TLEOrbitProvider(line1, line2),
    IGRFMagneticField(),
    NRLMSISAtmosphere(activity=SpaceWeatherInputs(f107=150.0, f107a=150.0, ap=4.0)),
)
```

注意：示例中的 TLE 和空间天气参数只是演示输入。正式分析时应使用目标任务对应的 TLE、epoch 和空间天气数据。

## 反作用轮研究实验

项目提供一个可复现的反作用轮阵列对比实验，包含标称四轮、单轮失效、低力矩约束、初始轮速偏置和 null-space 动量管理对比。

命令行运行：

```bash
satmodel-rw-study --output results/reaction_wheel_study --duration 20 --dt 0.02
```

或直接运行示例：

```bash
python examples/academic_reaction_wheel_study.py --duration 20 --dt 0.02
```

默认输出到 `results/reaction_wheel_study/`：

- `summary_metrics.csv`
- `time_history.csv`
- `README.md`
- `attitude_error.png`
- `allocation_error.png`
- `wheel_speed_norm.png`

## 测试与开发

运行测试：

```bash
pytest -q
```

构建本地 wheel/sdist：

```bash
python -m build
```

检查构建产物：

```bash
twine check dist/*
```

命令行 smoke test：

```bash
satmodel-rw-study --output results/smoke --duration 2 --dt 0.05 --no-plots
```

## 物理建模边界

当前默认模型适合第一版 ADCS 研究和控制验证，不等同任务级高保真仿真器。

已包含：

- 刚体姿态动力学。
- 反作用轮内部轮速状态。
- 轮组力矩/速度饱和。
- 轮组失效降级。
- 简化 LEO 环境。
- 常见一阶扰动力矩预算。

暂未包含：

- J2、三体、机动和高保真数值轨道传播。
- 柔性附件、多体关节和燃料晃动。
- 面元级气动和太阳光压。
- 地球反照、热辐射和半影。
- 反作用轮摩擦、jitter、电机电流环。
- 磁力矩器动量卸载闭环。

如果需要任务级精度，应替换默认质量属性、几何、环境后端和执行机构参数，并根据任务需要接入更高保真传播器。

## 文档

- [新手导览](docs/NEWCOMER_GUIDE.md)
- [项目总说明与物理建模](docs/PROJECT_GUIDE.md)
- [架构说明](docs/ARCHITECTURE.md)
- [路线图](docs/ROADMAP.md)
- [平台化路线与实施计划](docs/PLATFORM_PLAN.md)
- [参考资料](docs/REFERENCES.md)
- [物理模型架构](docs/physics/01_model_architecture.md)
- [刚体姿态模型](docs/physics/02_rigid_body_attitude_model.md)
- [环境与扰动模型](docs/physics/03_disturbance_environment_model.md)
- [反作用轮模型](docs/physics/04_reaction_wheel_model.md)
- [来源与参数追溯](docs/physics/05_sources_and_parameter_traceability.md)

## License

MIT
