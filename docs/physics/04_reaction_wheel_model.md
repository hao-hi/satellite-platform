# 04 反作用轮模型

## 单轮状态

第 `i` 个反作用轮由自旋轴 `a_i`、自旋惯量 `J_i`、轮速 `Omega_i` 和电机力矩 `u_i` 描述：

$$
h_i = J_i\Omega_i
$$

$$
J_i\dot{\Omega}_i = u_i
$$

CubeSat 被控对象中的 `ReactionWheelStateEffector` 负责轮组分配和受限电机力矩，再让轮速状态与刚体状态在同一步 RK4 中传播。公开的独立轮组离散传播路径已删除，避免同一轮组被控对象有两套状态演化口径。

轮的动量容量由速度上限给出：

$$
h_{i,max}=J_i\Omega_{i,max}
$$

这也是本轮新增遥测字段 `wheel_momentum_capacity_nms` 的物理含义。

单轮硬件边界写成：

$$
|u_i|\le u_{i,max},\qquad |\Omega_i|\le\Omega_{i,max}
$$

默认分配器会在分配前先把速度上限换成本步可用电机力矩窗口：

$$
J_i\frac{-\Omega_{i,max}-\Omega_i}{\Delta t}
\le u_i \le
J_i\frac{\Omega_{i,max}-\Omega_i}{\Delta t}
$$

再和电机力矩上限取交集：

$$
u_{i,low}
=\max\left(-u_{i,max},J_i\frac{-\Omega_{i,max}-\Omega_i}{\Delta t}\right)
$$

$$
u_{i,high}
=\min\left(u_{i,max},J_i\frac{\Omega_{i,max}-\Omega_i}{\Delta t}\right)
$$

当 `dt=0` 或 `dt=None` 时，只使用电机力矩上限，不使用速度余量窗口。

## 轮组分配

把每个轮轴作为列向量构成：

$$
A = \begin{bmatrix} a_1 & a_2 & \cdots & a_n \end{bmatrix}
$$

正电机力矩增加轮自身动量，卫星本体获得反向力矩：

$$
\tau_B = -Au
$$

当前支持三种分配模式：

| 模式 | 用途 |
| --- | --- |
| `pinv` | 保留原始 Moore-Penrose 伪逆，用于教学和回归 |
| `bounded_pinv` | 默认模式，考虑单轮力矩上限、轮速余量和失效轮 |
| `nullspace_momentum` | 在有界分配基础上用冗余自由度把轮速拉向参考值 |

`pinv` 模式把控制器给出的本体系力矩命令 `tau_cmd` 用伪逆分配为：

$$
u^* = -A^+\tau_{cmd}
$$

对满行秩轮轴矩阵，Moore-Penrose 伪逆可写成：

$$
A^+=A^T(AA^T)^{-1}
$$

`bounded_pinv` 模式求解同一个力矩方程，但加入每个轮的当前可行窗口：

$$
A u \approx -\tau_{cmd},\qquad
u_{low}\le u\le u_{high}
$$

实现采用轻量主动集：先求最小范数解，若某些轮超出窗口，则把这些轮固定在边界上，再用剩余自由轮分配残余力矩。若失效或饱和导致无法完全实现命令，分配器返回最接近可实现的力矩，并通过 `allocation_error_nm` 暴露残差：

$$
e_\tau=\tau_{cmd}-\tau_{applied}
$$

四轮或更多冗余阵列的所有未约束解还可写成：

$$
u=-A^+\tau_{cmd}+(I-A^+A)z
$$

其中 `z` 位于轮组零空间。`nullspace_momentum` 模式使用这个自由度做内部动量管理。它构造目标电机力矩：

$$
u_{ref,i}=k_hJ_i(\Omega_{ref,i}-\Omega_i)
$$

然后在满足 `A u=-tau_cmd` 和本步可行窗口的前提下，让实际 `u` 尽量靠近 `u_ref`。对三轮正交这类无冗余构型，零空间维度为零，行为会自动退化为普通有界分配。

饱和和故障之后的实际本体力矩为：

$$
\tau_{applied}=-A u_{limited}
$$

控制器和估算器看到的仍是 `tau_applied`。耦合动力学还读取轮组动量 `h_w`，单轮电机力矩、轮速、轮动量和饱和标志保存在 `WheelArrayTelemetry`。

未饱和且轮轴满秩时该残差应接近零；力矩饱和、轮速饱和或故障后它描述命令不可实现程度。遥测还会记录本步可用力矩窗口、自由轮掩码、启用轮轴秩、请求力矩和可实现力矩。

## 支持的轮组配置

### 三轮正交

$$
A_{3rw}=I_3
$$

该配置无冗余，适合作为最小三轴控制基线。

### 四轮金字塔首版

首版轴向使用参考 CubeSat 项目的归一化对角方向：

$$
a_1=\frac{[1,1,1]^T}{\sqrt{3}},\quad
a_2=\frac{[1,-1,-1]^T}{\sqrt{3}}
$$

$$
a_3=\frac{[-1,1,-1]^T}{\sqrt{3}},\quad
a_4=\frac{[-1,-1,1]^T}{\sqrt{3}}
$$

这是一个冗余四轮基线，轮组可通过有界分配产生三轴力矩。轮失效会把禁用轮命令和实际力矩置零，并对剩余启用轮重新计算秩。若剩余三轮仍满秩，四轮金字塔可退化为能力降低但仍具备三轴控制的模式；若秩低于三，轮组返回最接近可实现力矩并报告非零分配残差。

## 与完整 gyrostat 方程的关系

标准轮控卫星方程常把轮组动量显式写入卫星总角动量。若：

$$
h_w^B=A J_w\Omega
$$

则一个常见刚体加轮组方程口径是：

$$
I_B\dot{\omega}
+\omega\times(I_B\omega+h_w^B)
=
\tau_{ext}
-A u
$$

其中 `u=J_w\dot{\Omega}`。`ReactionWheelStateEffector` 当前就在 CubeSat 路径使用该口径：`SpacecraftDynamics` 在传播时读取 `h_w`，电机力矩给出 `-Au` 的本体反作用，并保留轮速、动量容量和饱和诊断。

## 限幅口径

首版实现：

- 单轮电机力矩限制 `|u_i| <= u_max`
- 单轮轮速限制 `|Omega_i| <= Omega_max`
- 默认有界分配在命令进入单轮前就考虑本步速度余量
- 到达轮速上限或本步将到达上限时，`speed_saturated` 会置位
- 禁用轮的实际电机力矩为零
- `pinv` 模式保留原始未约束分配，再由单轮限幅保护硬件边界
- `nullspace_momentum` 只做轮组内部动量整形，不等于外部动量卸载

首版未实现：

- 轴承摩擦和 Coulomb friction
- 轮系抖振
- 电机电流环和转矩一阶滞后
- 安装误差
- 磁力矩器动量卸载
- 外部 QP/凸优化分配、最小无穷范数分配和完整失效重构控制

## 来源映射

| 内容 | 来源 | 本轮采用方式 |
| --- | --- | --- |
| 反作用轮作为带速度与力矩限制的执行机构 | [Basilisk reaction wheel state effector](https://avslab.github.io/basilisk/Documentation/simulation/dynamics/reactionWheels/reactionWheelStateEffector.html) | 轮速、命令和遥测边界参考 |
| 四轮金字塔、伪逆分配、失效演示 | [reaction wheel CubeSat demo](https://github.com/brunopinto900/attitude_control_reaction_wheels) | 轴向与参数基线参考，代码重写 |
| 三轮/四轮构型、动量管理 | Lee et al., "A study of reaction wheel configurations for a 3-axis satellite attitude control" | 说明配置和动量管理是联合设计问题 |
| 轮组力矩/动量包络 | Markley et al., "Maximum Torque and Momentum Envelopes for Reaction Wheel Arrays" | 后续约束分配和容量包络依据 |
| 轮系不确定和鲁棒控制背景 | Chen and Hu 2023，Hasan et al. 2022 | 后续控制研究锚点 |
| 四轮分配的更高级约束讨论 | 金字塔轮组分配文献，例如最小无穷范数方向 | 后续，不在本轮 |

## 当前默认数值

`ReactionWheelArrayConfig.pyramid_4wheel()` 默认使用：

| 参数 | 值 | 分类 |
| --- | --- | --- |
| 单轮自旋惯量 | `2.6e-5 kg m^2` | 参考项目推导值 |
| 单轮最大力矩 | `0.007 N m` | 参考项目 |
| 单轮最大轮速 | `8000 rpm` 转成 `rad/s` | 参考项目 |
| 初始轮速 | `0 rad/s` | `satmodel` 工程假设 |
| 默认分配模式 | `bounded_pinv` | `satmodel` 工程选择 |
| 零空间目标轮速 | `0 rad/s` | 默认关闭时无影响 |
| 零空间增益 | `0` | 默认关闭 |
