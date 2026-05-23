# 02 刚体姿态模型

## 坐标系和符号

| 符号 | 含义 |
| --- | --- |
| `N` | 惯性/轨道外部计算所用参考系 |
| `B` | 卫星本体系 |
| `q_BN` | 当前实现的标量在前四元数 |
| `omega_BN_B` | 本体系表达的角速度 |
| `I_B` | 本体系惯量矩阵 |
| `tau_B` | 本体系控制与扰动力矩 |

当前四元数约定由 `src/satmodel/math.py` 固定：方向余弦矩阵 `body_to_inertial_dcm(q)` 把本体系向量旋到惯性系，`inertial_to_body_dcm(q)` 为其转置。

## 当前实现公式

刚体姿态动力学写成：

$$
\dot{q}_{BN} = \frac{1}{2}\Omega(\omega_{BN}^{B})q_{BN}
$$

$$
I_B \dot{\omega}_{BN}^{B}
 =
\tau_B
 -
\omega_{BN}^{B} \times \left(I_B\omega_{BN}^{B}\right)
$$

其中：

$$
\Omega(\omega)=
\begin{bmatrix}
0 & -\omega_x & -\omega_y & -\omega_z \\
\omega_x & 0 & \omega_z & -\omega_y \\
\omega_y & -\omega_z & 0 & \omega_x \\
\omega_z & \omega_y & -\omega_x & 0
\end{bmatrix}
$$

若把四元数分为标量部 `q_0` 与向量部 `q_v`，当前方向余弦矩阵也可写成：

$$
C_{NB}(q)=
\left(q_0^2-q_v^\top q_v\right)I_3
+2q_vq_v^\top
+2q_0[q_v]_\times
$$

其中 `[x]_\times y=x\times y`。代码中 `body_to_inertial_dcm()` 与该形式等价，`inertial_to_body_dcm()` 使用其转置。

代码中 `SpacecraftDynamics.angular_acceleration()` 解线性方程得到 `dot(omega)`，`RK4Integrator` 固定步长传播，步末重新归一化四元数。

## 质量属性

首版新增：

```python
MassProperties(
    mass_kg=...,
    inertia_body_kgm2=...,
    center_of_mass_body_m=...,
)
```

`CubeSatPhysicalConfig.one_unit_reaction_wheel_demo()` 的惯量先用均匀盒体惯量，再加参考轮组的偏置质量近似：

$$
I_{box,x}=\frac{m}{12}(l_y^2+l_z^2)
$$

$$ 
I_{demo} = I_{bus} + N_{rw}m_{rw}r_{off}^2 I_3
$$

这个偏置项是首版量级近似，等价于把四个轮质量的平行轴贡献折成各向同性项。若后续要研究安装位置或非对称结构，需使用每个部件的完整平行轴张量。

完整平行轴写法是：

$$
I_{P}=I_{C}+m\left((d^\top d)I_3-dd^\top\right)
$$

其中 `d` 是部件质心相对总参考点的位置。这个式子是后续把轮、电池、载荷和展开件逐件并入质量属性的基础。

## 资料来源和边界

| 项 | 来源 | 当前用途 |
| --- | --- | --- |
| 四元数运动学、欧拉刚体方程 | Wertz, *Spacecraft Attitude Determination and Control*；Schaub and Junkins, *Analytical Mechanics of Space Systems* | 当前刚体 truth model |
| 刚体 attitude truth model 边界 | [GMAT Spacecraft Attitude](https://documentation.help/GMAT/SpacecraftAttitude.html) | 说明刚体建模并非柔性高保真模型 |
| 多体和柔性升级方向 | [NASA 42](https://github.com/ericstoneking/42)，Li et al. 2022，He and Cao 2023，Murilo et al. 2021 | 后续扩展，不在本轮 |
| 1U 质量、尺寸、轮偏置首版数值 | [CubeSat wheel demo `config.py`](https://github.com/brunopinto900/attitude_control_reaction_wheels/blob/main/config.py) | 当前 demo mass model |

## 当前假设

- 卫星本体为刚体。
- 本轮不传播轨道平动状态。
- 本轮不建柔性模态、晃动、结构阻尼和关节附件。
- `SpacecraftDynamics.inertia_provider` 仍保留时变惯量扩展口。
