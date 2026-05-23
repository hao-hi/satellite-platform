# satmodel

`satmodel` 是一个面向卫星姿态控制研究的轻量 Python 库。它把刚体姿态动力学、轨道环境、扰动力矩、执行机构、传感器、估计器、控制器和仿真实验拆成可组合模块，适合做 CubeSat/小卫星 ADCS 的第一版建模、控制律验证和论文式数值实验。

当前项目已经整理为标准 `src/` layout Python 包。安装后可以直接：

```python
import satmodel
```

也可以从子模块按需装配更细的物理模型。

初次接触项目时，建议先阅读 [新手导览](docs/NEWCOMER_GUIDE.md)。它按“项目是什么、源码文件作用、示例脚本作用、如何运行”的顺序介绍仓库。

## 当前能力

- 标量在前四元数刚体姿态动力学
- 固定步长 RK4 姿态传播器
- 圆轨道、Kepler 轨道、星历表/callable 轨道源
- 可选 TLE/SGP4 轨道适配器
- 组合式 LEO 环境：地磁场、大气密度、太阳方向、地影
- 中心偶极地磁和指数大气默认模型
- 可选 IGRF 和 NRLMSIS 高保真环境适配入口
- 重力梯度、残余磁、气动、太阳光压扰动力矩
- 理想体轴力矩执行器
- 三轮正交与四轮金字塔反作用轮状态效应器
- 多轮反作用轮有界分配、失效降级和 null-space 动量管理
- 简化姿态传感器和陀螺模型
- MEKF 姿态估计
- 可选对角惯量 RLS 辨识
- PD 和 LADRC 姿态控制器
- 网格、随机、Nelder-Mead、模拟退火和 PSO 调参工具
- 可复现反作用轮阵列论文式仿真实验

## 安装方式

开发安装：

```bash
pip install -e .
```

包含测试、构建和发布检查工具：

```bash
pip install -e ".[dev]"
```

包含绘图能力：

```bash
pip install -e ".[plot]"
```

包含可选地球环境模型和 TLE 适配器：

```bash
pip install -e ".[earth,tle]"
```

说明：

- 默认安装只依赖 `numpy` 和 `matplotlib`，保持轻量。
- `earth` 额外安装 `ppigrf` 和 `pymsis`，只在实例化 IGRF/NRLMSIS 适配器时需要。
- `tle` 额外安装 `sgp4`，只在使用 `TLEOrbitProvider` 时需要。

## 快速开始

最短闭环仿真：

```python
from satmodel import ScenarioRunner, SimulationConfig, build_default_system

system = build_default_system(controller="pd", identify_inertia=True)
config = SimulationConfig(duration=5.0, dt=0.02)
result = ScenarioRunner(system).run(config)

print(result.metrics(config.reference))
```

输出指标包括初始误差、末端误差、RMS 姿态误差、力矩积分和峰值力矩。

## 作为库调用

推荐的高层 API：

```python
from satmodel import (
    __version__,
    ScenarioRunner,
    SimulationConfig,
    build_default_system,
    build_cubesat_reaction_wheel_system,
)
```

示例：构造一个 1U CubeSat 反作用轮闭环系统。

```python
from satmodel import ScenarioRunner, SimulationConfig, build_cubesat_reaction_wheel_system

system = build_cubesat_reaction_wheel_system(controller="pd")
result = ScenarioRunner(system).run(SimulationConfig(duration=10.0, dt=0.02))

print(result.metrics())
print(result.wheel_speeds_rad_s.shape)
print(result.wheel_allocation_error_nm.max())
```

高级用户可以直接从子模块导入组件：

```python
from satmodel.environment import CircularOrbitProvider, OrbitalEnvironment
from satmodel.actuators import ReactionWheelArrayConfig, ReactionWheelStateEffector
from satmodel.disturbances import GravityGradientTorque, default_leo_disturbance_effectors
from satmodel.physics import CubeSatPhysicalConfig
```

内部工具模块以下划线开头，例如 `satmodel._validation`，不作为公共 API 使用。

## 反作用轮 CubeSat 示例

运行四轮金字塔反作用轮 PD 闭环：

```bash
python examples/cubesat_reaction_wheels_pd.py
```

运行单轮失效 smoke scenario：

```bash
python examples/cubesat_wheel_failure.py
```

反作用轮系统会记录：

- `result.wheel_speeds_rad_s`
- `result.wheel_torques_nm`
- `result.wheel_momentum_nms`
- `result.wheel_momentum_capacity_nms`
- `result.wheel_allocation_error_nm`
- `result.wheel_saturation_flags`

当前轮组默认使用 `bounded_pinv` 分配：在分配时考虑单轮力矩上限、当前轮速、本步速度余量和失效轮。冗余轮组还可以使用 `nullspace_momentum` 做内部轮速/动量分布管理。

## 论文式仿真实验复现

库中提供一个可复现的反作用轮阵列研究实验，包含标称四轮、单轮失效、低力矩约束、带初始轮速偏置和 null-space 动量管理对比。

作为 Python API 调用：

```python
from satmodel.studies import run_reaction_wheel_study

rows = run_reaction_wheel_study(
    output_dir="results/reaction_wheel_study",
    duration=20.0,
    dt=0.02,
)
```

作为命令行调用：

```bash
satmodel-rw-study --output results/reaction_wheel_study --duration 20 --dt 0.02
```

或直接运行示例脚本：

```bash
python examples/academic_reaction_wheel_study.py --duration 20 --dt 0.02
```

默认输出：

- `summary_metrics.csv`：汇总指标表
- `time_history.csv`：全时域数据
- `README.md`：方法、结果表和解释
- `attitude_error.png`
- `allocation_error.png`
- `wheel_speed_norm.png`

这些文件默认写入 `results/reaction_wheel_study/`。`results/` 被视为运行产物，默认不纳入源码管理。

## 主要模块说明

| 模块 | 作用 |
| --- | --- |
| `satmodel.system` | 高层系统装配、单速率仿真循环 |
| `satmodel.types` | 状态、测量、环境上下文、结果和 telemetry 数据对象 |
| `satmodel.dynamics` | 刚体姿态动力学和 RK4 积分 |
| `satmodel.environment` | 轨道源、地磁/大气后端、环境采样 |
| `satmodel.disturbances` | 重力梯度、残余磁、气动、SRP 扰动力矩 |
| `satmodel.actuators` | 理想力矩执行器和反作用轮状态效应器 |
| `satmodel.physics` | 质量属性、CubeSat 几何和物理配置 |
| `satmodel.sensors` | 姿态传感器和陀螺简化模型 |
| `satmodel.estimation` | MEKF 和估计器组合 |
| `satmodel.identification` | 对角惯量 RLS 和角加速度辅助 |
| `satmodel.controllers` | PD 和 LADRC 控制器 |
| `satmodel.optimization` | 参数调优工具 |
| `satmodel.studies` | 可复现实验与研究脚本的库化入口 |

## 物理建模边界

当前默认模型适合第一版 ADCS 研究和控制验证，不等同任务级高保真仿真器。

已包含：

- 刚体姿态动力学
- 反作用轮内部轮速状态
- 轮组力矩/速度饱和
- 轮组失效降级
- 简化 LEO 环境
- 常见一阶扰动力矩预算

暂未包含：

- J2/三体/机动数值轨道传播
- 柔性附件和多体关节动力学
- 燃料晃动
- 面元级气动和 SRP
- 地球反照、热辐射和半影
- 反作用轮摩擦、jitter、电机电流环
- 磁力矩器动量卸载闭环

如果需要任务级精度，应替换默认质量属性、几何、环境后端和执行机构参数，并根据任务需要接入更高保真传播器。

## 示例脚本

```bash
python examples/open_loop.py
python examples/pd_closed_loop.py
python examples/ladrc_closed_loop.py
python examples/mekf_rls_identification.py
python examples/tune_pd.py
python examples/cubesat_reaction_wheels_pd.py
python examples/cubesat_wheel_failure.py
python examples/academic_reaction_wheel_study.py --duration 2 --dt 0.05 --no-plots
```

部分闭环和辨识示例支持 `--plot`。

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

命令行实验 smoke test：

```bash
satmodel-rw-study --output results/smoke --duration 2 --dt 0.05 --no-plots
```

## 文档索引

- [项目总说明与物理建模](docs/PROJECT_GUIDE.md)
- [架构说明](docs/ARCHITECTURE.md)
- [路线图](docs/ROADMAP.md)
- [参考资料](docs/REFERENCES.md)
- [物理模型架构](docs/physics/01_model_architecture.md)
- [刚体姿态模型](docs/physics/02_rigid_body_attitude_model.md)
- [环境与扰动模型](docs/physics/03_disturbance_environment_model.md)
- [反作用轮模型](docs/physics/04_reaction_wheel_model.md)
- [来源与参数追溯](docs/physics/05_sources_and_parameter_traceability.md)
