# 新手导览

本文面向第一次打开本项目的人，先回答三个问题：

- 这个项目是什么。
- 每个源码文件大致负责什么。
- 示例脚本应该怎么跑、各自展示什么能力。

如果需要更完整的物理公式、默认参数和资料来源，请继续阅读 [项目总说明](PROJECT_GUIDE.md) 和 [架构说明](ARCHITECTURE.md)。

## 1. 项目是什么

`satmodel` 是一个面向卫星姿态控制研究的轻量 Python 仿真库。它主要服务于 CubeSat/小卫星 ADCS 的第一版建模、控制律验证和论文式数值实验。

项目把姿态仿真拆成一组可替换组件：

```text
轨道环境 -> 扰动力矩 -> 传感器测量 -> 姿态估计 -> 控制器 -> 执行机构 -> 刚体动力学传播
```

当前重点能力包括：

- 刚体卫星姿态传播。
- 轨道环境、地磁、大气、太阳方向和地影采样。
- 重力梯度、残余磁、气动和太阳光压扰动力矩。
- 理想体轴力矩执行器和反作用轮轮组执行器。
- 简化姿态传感器和陀螺模型。
- MEKF 姿态估计。
- 可选对角惯量 RLS 辨识。
- PD 和 LADRC 姿态控制。
- 反作用轮饱和、失效降级和动量管理分析。
- 可复现的反作用轮阵列研究实验。

需要注意的是，当前默认模型偏轻量，适合快速研究和验证，不等同任务级高保真仿真器。若用于具体任务，应替换为目标卫星的质量属性、几何、硬件参数、轨道源和高保真环境模型。

## 2. 推荐阅读入口

初次上手建议按下面顺序读：

1. [README.md](../README.md)：安装、快速开始和主要功能。
2. [PROJECT_GUIDE.md](PROJECT_GUIDE.md)：物理模型、状态、单位和参数说明。
3. [ARCHITECTURE.md](ARCHITECTURE.md)：组件分层和单步仿真数据流。
4. `examples/pd_closed_loop.py`：最小闭环控制例子。
5. `src/satmodel/system.py`：高层系统装配和仿真循环。

如果只想先跑通项目，可以先跳到第 5 节。

## 3. 源码文件作用

核心源码位于 `src/satmodel/`。

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 顶层公共 API 汇总，使用户可以直接 `from satmodel import ScenarioRunner`。 |
| `_version.py` | 包版本号。 |
| `_validation.py` | 输入校验工具，例如三维向量、三阶矩阵、单位向量和 UTC 时间校验。 |
| `types.py` | 核心数据对象，包括刚体状态、参考姿态、轨道状态、环境上下文、力矩预算、传感器测量、估计状态、仿真配置、仿真结果和反作用轮遥测。 |
| `math.py` | 姿态数学工具，包括四元数归一化、乘法、求逆、轴角转换、姿态误差和方向余弦矩阵。 |
| `geometry.py` | 共享几何模型，目前主要是盒体几何和投影面积计算。 |
| `physics.py` | 质量属性和物理配置，包括均匀盒体惯量、演示 CubeSat 质量属性和 1U 反作用轮配置入口。 |
| `environment.py` | 环境层，包括圆轨道、Kepler 轨道、星历轨道、可选 TLE/SGP4、中心偶极地磁、可选 IGRF、指数大气、可选 NRLMSIS 和组合式轨道环境采样。 |
| `disturbances.py` | 扰动力矩模型，包括重力梯度、残余磁、气动、太阳光压和扰动集合。 |
| `actuators.py` | 执行机构模型，包括理想力矩执行器、反作用轮配置、轮组力矩分配、速度/力矩限幅、失效轮处理和遥测输出。 |
| `dynamics.py` | 刚体姿态动力学和 RK4 积分器，支持反作用轮状态效应器的内部动量耦合。 |
| `sensors.py` | 简化姿态传感器和陀螺模型，支持噪声、偏置和随机种子复现。 |
| `estimation.py` | 姿态估计层，包括 MEKF 和估计器组合。 |
| `identification.py` | 惯量辨识工具，包括角加速度辅助、扰动重构、对角惯量 RLS 和诊断量。 |
| `controllers.py` | 控制器，包括 PD 控制器和三轴 LADRC 控制器。 |
| `optimization.py` | 参数优化工具，包括网格搜索、随机搜索、Nelder-Mead、模拟退火和 PSO。 |
| `plotting.py` | 仿真结果绘图辅助函数。 |
| `system.py` | 高层入口，负责装配卫星系统、执行单速率固定步长仿真、提供默认系统构造器。 |
| `studies/__init__.py` | 研究实验子包的公共入口。 |
| `studies/reaction_wheel_study.py` | 反作用轮阵列研究实验，生成多场景对比结果、CSV、图像和 Markdown 报告。 |

其中最重要的文件是 `system.py`。它提供：

- `SatelliteSystem`：把动力学、环境、传感器、执行机构、估计器、控制器和扰动模型装成一个系统。
- `ScenarioRunner`：按固定步长运行仿真，并收集结果。
- `build_default_system()`：构造默认理想力矩执行器系统。
- `build_cubesat_reaction_wheel_system()`：构造 1U CubeSat 反作用轮系统。

## 4. 示例脚本作用

示例脚本位于 `examples/`。

| 脚本 | 作用 |
| --- | --- |
| `open_loop.py` | 最简单的开环刚体传播，不启用控制器，适合检查动力学是否能跑通。 |
| `pd_closed_loop.py` | 使用 PD 控制器做姿态稳定，是最适合入门的闭环例子。 |
| `ladrc_closed_loop.py` | 使用 LADRC 控制器，并加入额外扰动，展示扰动补偿和控制器诊断。 |
| `mekf_rls_identification.py` | 使用 MEKF 姿态估计和 RLS 对角惯量辨识，输出最终惯量估计。 |
| `tune_pd.py` | 用 PSO 优化 PD 参数，展示 `optimization.py` 的调参接口。 |
| `cubesat_reaction_wheels_pd.py` | 构造 1U CubeSat 四反作用轮系统，用 PD 控制姿态，并输出轮速、分配误差和饱和计数。 |
| `cubesat_wheel_failure.py` | 禁用一个反作用轮后运行短场景，展示轮组失效降级和遥测。 |
| `academic_reaction_wheel_study.py` | 反作用轮阵列论文式研究入口，批量运行多种轮组场景并写出结果文件。 |

## 5. 如何使用

在项目根目录执行：

```powershell
cd "E:\Desktop\卫星姿控仿真 (5.22)"
```

开发安装：

```powershell
pip install -e .
```

如果需要测试、构建和绘图能力：

```powershell
pip install -e ".[dev,plot]"
```

运行最简单开环例子：

```powershell
python examples\open_loop.py
```

运行 PD 闭环：

```powershell
python examples\pd_closed_loop.py
```

带图运行 PD 闭环：

```powershell
python examples\pd_closed_loop.py --plot
```

运行 CubeSat 反作用轮闭环：

```powershell
python examples\cubesat_reaction_wheels_pd.py
```

运行反作用轮失效场景：

```powershell
python examples\cubesat_wheel_failure.py
```

运行完整反作用轮研究实验：

```powershell
python examples\academic_reaction_wheel_study.py --duration 20 --dt 0.02
```

安装后也可以使用命令行入口：

```powershell
satmodel-rw-study --output results/reaction_wheel_study --duration 20 --dt 0.02
```

运行测试：

```powershell
pytest -q
```

## 6. 最小代码模板

默认理想力矩执行器闭环：

```python
from satmodel import ScenarioRunner, SimulationConfig, build_default_system

system = build_default_system(controller="pd", identify_inertia=True)
config = SimulationConfig(duration=5.0, dt=0.02)

result = ScenarioRunner(system).run(config)

print(result.metrics(config.reference))
```

CubeSat 反作用轮闭环：

```python
from satmodel import ScenarioRunner, SimulationConfig, build_cubesat_reaction_wheel_system

system = build_cubesat_reaction_wheel_system(controller="pd")
config = SimulationConfig(duration=6.0, dt=0.02)

result = ScenarioRunner(system).run(config)

print(result.metrics(config.reference))
print(result.wheel_speeds_rad_s.shape)
print(result.wheel_allocation_error_nm.max())
```

## 7. 常见输出怎么看

`SimulationResult.metrics()` 返回常用性能指标：

| 指标 | 含义 |
| --- | --- |
| `initial_error_deg` | 初始姿态误差，单位 deg。 |
| `final_error_deg` | 末端姿态误差，单位 deg。 |
| `rms_error_deg` | RMS 姿态误差，单位 deg。 |
| `effort_nms` | 控制力矩积分，近似表示控制消耗，单位 N m s。 |
| `peak_torque_nm` | 最大实际执行力矩，单位 N m。 |

反作用轮系统还可以查看：

| 属性 | 含义 |
| --- | --- |
| `result.wheel_speeds_rad_s` | 各轮轮速时间序列。 |
| `result.wheel_torques_nm` | 各轮实际力矩。 |
| `result.wheel_torque_commands_nm` | 各轮命令力矩。 |
| `result.wheel_momentum_nms` | 各轮角动量。 |
| `result.wheel_allocation_error_nm` | 轮组无法满足本体系命令时的分配误差。 |
| `result.wheel_saturation_flags` | 轮组力矩或速度饱和标志。 |

## 8. 下一步怎么改

如果你要扩展项目，通常从下面几类入口开始：

- 改控制律：看 `controllers.py`，然后在 `build_default_system()` 或示例脚本中替换控制器。
- 改卫星惯量和几何：看 `physics.py`、`geometry.py` 和 `CubeSatPhysicalConfig`。
- 改执行机构：看 `actuators.py`，尤其是 `ReactionWheelArrayConfig` 和 `ReactionWheelStateEffector`。
- 改扰动力矩：看 `disturbances.py`，添加新的 disturbance effector。
- 改环境模型：看 `environment.py`，替换 orbit provider、magnetic field backend 或 atmosphere backend。
- 做批量实验：参考 `studies/reaction_wheel_study.py`，把实验逻辑库化，再用 `examples/` 写薄入口。
