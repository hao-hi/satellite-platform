# 03 环境与扰动力矩模型

## 当前实现概览

当前环境层由 `OrbitalEnvironment` 组合轨道源与外部场后端。默认
`build_demo_leo_environment()` 仍选用：

- `CircularOrbitProvider` 的圆轨道位置与速度
- 固定太阳方向
- 圆柱地影判断
- `CenteredDipoleMagneticField` 的地球中心偶极地磁场
- `ExponentialAtmosphere` 的指数大气密度

该层返回 `EnvironmentContext`，同时携带绝对 `epoch_utc` 和
`GeodeticPoint`。轨道源可切换到 `KeplerianOrbitProvider`、
`EphemerisOrbitProvider` 或可选 `TLEOrbitProvider`；地磁和大气后端可切换
到可选 `IGRFMagneticField` 与 `NRLMSISAtmosphere`。重力梯度、残余磁、
气动和太阳光压由 `DisturbanceEffectorSet` 中的具名 effectors 根据上下文求出，
再汇总为 `TorqueBudget`。气动和太阳光压读取共享 `BoxGeometry`，不再各自
复制盒体尺寸。

## 圆轨道与环境场

圆轨道半径和平均角速度：

$$
r = R_E + h,\qquad n = \sqrt{\frac{\mu_E}{r^3}}
$$

首版轨道面内位置和速度先由 argument of latitude `u=u_0+nt` 得到：

$$
r_{pf}=r[\cos u,\sin u,0]^T
$$

$$
v_{pf}=\sqrt{\frac{\mu_E}{r}}[-\sin u,\cos u,0]^T
$$

再用 RAAN 和 inclination 旋到当前 ECI-like 参考系。

地磁偶极场：

$$
B_N =
\frac{\mu_0}{4\pi r^3}
\left(3\hat{r}(m_E^\top \hat{r})-m_E\right)
$$

指数大气密度：

$$
\rho(h)=\rho_{400}\exp\left(-\frac{h-400~km}{H}\right)
$$

该大气模型只适合首版扰动力矩量级研究。可选 `NRLMSISAtmosphere` 适配器
需要绝对时间、地理位置、太阳和地磁活动输入；项目不在线下载空间天气数据，
而由配置或用户 provider 给出这些输入。

固定太阳方向的圆柱地影判断可概括为：

$$
r^\top \hat{s}<0,\qquad
\left\|r-(r^\top \hat{s})\hat{s}\right\|\le R_E
$$

满足两式时当前模型把 SRP 关断。

## 四类扰动力矩

### 重力梯度

`GravityGradientTorque` 使用：

$$
\tau_{gg}^{B} =
\frac{3\mu_E}{\left\|r\right\|^3}
\hat{r}_B \times \left(I_B\hat{r}_B\right)
$$

它把轨道径向单位向量旋到本体系，再用当前惯量矩阵计算。

若本体系恰好与惯量主轴对齐，并记 `rhat_B=[r_x,r_y,r_z]^T`，则该式还能写成分量形式：

$$
\tau_{gg}^{B}
=
\frac{3\mu_E}{r^3}
\begin{bmatrix}
(I_z-I_y)r_yr_z\\
(I_x-I_z)r_zr_x\\
(I_y-I_x)r_xr_y
\end{bmatrix}
$$

### 残余磁矩

`ResidualMagneticTorque` 使用：

$$
\tau_{mag}^{B}=m_{res}^{B}\times B_B
$$

当前只建残余磁矩扰动，不建磁力矩器执行器。主动磁控和轮组动量卸载会在磁力矩器子系统中实现。

后续磁力矩器将沿用同一叉乘结构：

$$
\tau_{mtq}^{B}=m_{cmd}^{B}\times B_B
$$

因此地磁精度和磁矩上限会同时影响主动磁控与残余磁扰动研究。

### 气动力矩

相对大气速度：

$$
v_{rel,N}=v_N-\omega_E\times r_N
$$

盒体投影面积：

$$
A_{box}(n_B)=l_y l_z|n_x|+l_x l_z|n_y|+l_x l_y|n_z|
$$

`AerodynamicTorque` 使用拖曳力和力矩：

$$
F_{drag}^{B}=-\frac{1}{2}\rho C_D A_{box}\left\|v_{rel,B}\right\|v_{rel,B}
$$

$$
\tau_{aero}^{B}=r_{cp,aero}^{B}\times F_{drag}^{B}
$$

后续 facet 版本会把盒体投影替换为可见迎风面求和。对第 `i` 个面元可先写成：

$$
A_{i,\perp}=A_i\max(0,-n_i^\top \hat{v}_{rel})
$$

再按面元作用点累加力矩：

$$
\tau_{aero}^{B}=\sum_i (r_i-r_{cm})\times F_{drag,i}^{B}
$$

### 太阳光压力矩

`SolarPressureTorque` 在非地影时给出：

$$
F_{srp}^{B}=-P_{\odot}C_R A_{box}(s_B)\hat{s}_B
$$

$$
\tau_{srp}^{B}=r_{cp,srp}^{B}\times F_{srp}^{B}
$$

这里的符号遵循当前代码 `sun_vector_eci` 的约定。后续 facet SRP 应显式区分面元法向、入射方向、吸收、镜面反射和漫反射。

面元升级时，至少要改为逐面求和：

$$
\tau_{srp}^{B}=\sum_i (r_i-r_{cm})\times F_{srp,i}^{B}
$$

其中 `F_{srp,i}` 由入射角和面元光学系数给出，不能再用单一 `C_R` 和盒体中心压心代替。

## 来源和升级口径

| 模型 | 当前公式来源 | 后续升级资料 |
| --- | --- | --- |
| 重力梯度力矩 | Wertz 的刚体扰动模型写法 | Basilisk gravity-gradient effector 方向 |
| 残余磁矩力矩 | 小卫星磁扰动常用 `m x B` 关系；Ovchinnikov and Roldugin 2019 作为磁控背景 | 可选 NOAA IGRF 适配器，后续磁力矩器和动量卸载 |
| 首版气动与 SRP | 小卫星扰动预算的一阶盒体表达；当前项目继承简化实现 | [Basilisk facet drag/SRP](https://avslab.github.io/basilisk/) 和 Tudat panelled macromodel |
| 大气密度 | 默认指数模型；可选 NRLMSIS 适配器 | [NASA CCMC NRLMSIS 2.1](https://ccmc.gsfc.nasa.gov/models/NRLMSIS~2.1) |
| 地磁场 | 默认中心偶极；可选 IGRF 适配器 | [NOAA IGRF](https://www.ncei.noaa.gov/products/international-geomagnetic-reference-field) |

## 当前默认参数分类

| 参数组 | 当前口径 |
| --- | --- |
| `mu_earth`、`earth_radius_m` | 当前环境场后端和重力梯度 effector 的 Earth constants |
| `altitude_m=400 km`、`inclination_deg=51.6` | `satmodel` 工程假设的 LEO demo |
| 密度参考值、尺度高度 | `ExponentialAtmosphere` 工程假设 |
| `earth_rotation_rad_s`、CP 偏置、残余磁矩 | disturbance effector 工程假设，用于保持扰动力矩非零和量级可观察 |
| `solar_pressure_n_m2` | SRP effector 的 1 AU 光压工程常量 |

在高保真对比前，应把这些工程假设替换为任务参数、官方环境后端或可追溯试验数据。
