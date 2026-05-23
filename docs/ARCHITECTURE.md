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
