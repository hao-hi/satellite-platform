# 05 来源与参数追溯

## 使用规则

本项目把物理输入分成三类：

1. **文献/官方资料给定**：可直接追溯到论文、标准、官方文档或教材。
2. **参考项目给定**：用于首版演示的社区项目参数，需在 `v0.5` 高保真建模或具体任务适配时替换为目标硬件数据。
3. **`satmodel` 工程假设**：为了形成可运行基线而设的默认值，不能当作飞行硬件标称值。

## 公式追溯表

| 实现内容 | 公式 | 主要来源 |
| --- | --- | --- |
| 四元数运动学 | `qdot = 0.5 Omega(omega) q` | Wertz；Schaub and Junkins |
| 刚体欧拉方程 | `I wdot = tau - w x Iw` | Wertz；Schaub and Junkins |
| 均匀盒体惯量 | `Ix = m(ly^2+lz^2)/12` | 基础刚体动力学 |
| 轮速积分 | `J dot(Omega) = u` | 反作用轮基本角动量关系；Basilisk 轮模型边界 |
| 轮组分配 | `tau_B = -A u`, `u = -A+ tau_cmd` | 反作用轮控制分配基础；参考 CubeSat 轮组演示 |
| 轮动量容量 | `h_max = J Omega_max` | 反作用轮动量容量定义；Markley 等人的轮组包络研究 |
| 耦合轮组动力学 | `I wdot + w x (Iw+h_w) = tau_ext - A u` | 轮控卫星标准动力学；Lee 等人的构型研究 |
| 轨道径向单位矢量转本体系 | `rhat_B = C_BN r_N / norm(r_N)` | 刚体姿态坐标变换；Wertz；Schaub and Junkins |
| 重力梯度扰动力矩 | `tau_gg^B = 3 mu_E / r^3 * rhat_B x (I_B rhat_B)` | Wertz 环境扰动力矩模型；常用航天器重力梯度表达 |
| 主轴惯量下重力梯度分量 | `tau_gg = 3 mu_E/r^3 [(Iz-Iy)ry rz, (Ix-Iz)rz rx, (Iy-Ix)rx ry]^T` | Wertz 环境扰动力矩模型 |
| 地磁场转本体系 | `B_B = C_BN B_N` | 刚体姿态坐标变换；IGRF/偶极场作为外部场输入 |
| 残余磁矩扰动力矩 | `tau_mag^B = m_res^B x B_B` | 小卫星磁扰动常用 `m x B` 关系；Ovchinnikov and Roldugin 2019 |
| 气动相对速度 | `v_rel,N = v_N - omega_E x r_N` | 低轨大气随地球自转的一阶工程近似 |
| 盒体投影面积 | `A_box(n_B)=ly lz abs(nx) + lx lz abs(ny) + lx ly abs(nz)` | 盒体宏模型/一阶投影面积模型；Tudat spacecraft macromodels 升级路线 |
| 气动阻力 | `F_drag^B = -0.5 rho C_D A_box norm(v_rel,B) v_rel,B` | 小卫星一阶气动扰动预算；Basilisk/Tudat 面元拖曳为 `v0.5` 升级锚点 |
| 气动扰动力矩 | `tau_aero^B = r_cp,aero^B x F_drag^B` | 力矩定义 `r x F`；一阶压心偏置工程模型 |
| 圆柱地影判据 | `r^T s_hat < 0`, `norm(r - (r^T s_hat)s_hat) <= R_E` | 一阶圆柱地影模型；`v0.5` 可升级到半影/星历太阳模型 |
| 太阳光压力 | `F_srp^B = -P_sun C_R A_box(s_B) s_hat_B` | 小卫星一阶 SRP 扰动预算；Basilisk/Tudat 面元 SRP 为 `v0.5` 升级锚点 |
| 太阳光压扰动力矩 | `tau_srp^B = r_cp,srp^B x F_srp^B` | 力矩定义 `r x F`；一阶压心偏置工程模型 |
| 扰动力矩汇总 | `tau_dist^B = tau_gg + tau_mag + tau_aero + tau_srp + tau_extra + tau_noise` | `DisturbanceEffectorSet` 具名力矩预算；仿真 runner 汇总口径 |

## 默认参数追溯表

| 参数 | 当前值 | 分类 | 来源或说明 |
| --- | --- | --- | --- |
| 1U 演示总质量 | `2.6 kg` | 参考项目给定 | `brunopinto900` `config.py` |
| 1U 演示边长 | `0.1 m` | 参考项目给定 | `brunopinto900` `config.py` |
| 演示惯量使用的反作用轮质量 | `0.13 kg` | 参考项目给定 | `brunopinto900` `config.py` |
| 演示惯量使用的反作用轮偏置 | `0.04 m` | 参考项目给定 | `brunopinto900` `config.py` |
| 单轮自旋惯量 | `2.6e-5 kg m^2` | 参考项目推导值 | 参考项目 `J_RW = 0.5 M_RW (0.02)^2` |
| 单轮最大力矩 | `0.007 N m` | 参考项目给定 | 参考项目 `TAU_MAX` |
| 单轮最大轮速 | `8000 rpm` | 参考项目给定 | 参考项目 `RPM_MAX` |
| 单轮初始轮速 | `0 rad/s` | `satmodel` 工程假设 | 首版零动量起始点 |
| LEO altitude | `400 km` | `satmodel` 工程假设 | 当前简化环境默认 |
| LEO inclination | `51.6 deg` | `satmodel` 工程假设 | 当前简化环境默认 |
| exponential density reference | `4.0e-12 kg/m^3` | `satmodel` 工程假设 | 首版拖曳量级默认 |
| density scale height | `55 km` | `satmodel` 工程假设 | 首版拖曳量级默认 |
| 可选 IGRF 输入 | 历元、大地纬度/经度/高度 | 文献/官方资料给定 | `IGRFMagneticField` 适配器由 `OrbitalEnvironment` 供给 |
| 可选 NRLMSIS 输入 | 历元、大地位置、F10.7/F10.7a/AP | 文献/官方资料给定 | `NRLMSISAtmosphere` 不在线下载空间天气数据 |
| 重力梯度 `mu_earth` | `3.986004418e14 m^3/s^2` | 文献/官方资料给定 | 当前 `GravityGradientTorqueConfig` 使用的地球标准引力参数 |
| 残余磁偶极矩 | `[0.015, -0.010, 0.012] A m^2` | `satmodel` 工程假设 | 当前 `ResidualMagneticTorqueConfig` 默认值，用于产生非零磁扰动 |
| 气动阻力系数 `C_D` | `2.2` | `satmodel` 工程假设 | 小卫星首版扰动预算常用量级，不代表目标卫星实测值 |
| 气动压心偏置 | `[0.015, -0.008, 0.012] m` | `satmodel` 工程假设 | 当前 `AerodynamicTorqueConfig` 默认值，用于产生非零气动力矩 |
| 地球自转角速度 | `7.2921159e-5 rad/s` | 文献/官方资料给定 | 当前气动相对速度模型使用的地球自转角速度 |
| SRP 反射系数 `C_R` | `1.4` | `satmodel` 工程假设 | 首版 SRP 扰动预算常用量级，应按材料光学属性替换 |
| SRP 压心偏置 | `[-0.012, 0.010, 0.018] m` | `satmodel` 工程假设 | 当前 `SolarPressureTorqueConfig` 默认值，用于产生非零 SRP 力矩 |
| 太阳光压常量 | `4.56e-6 N/m^2` | 文献/工程资料给定 | 近 1 AU 太阳辐射压常用工程值 |
| 默认扰动盒体尺寸 | `[0.10, 0.20, 0.30] m` | `satmodel` 工程假设 | 未传入 `BoxGeometry` 时 `default_leo_disturbance_effectors()` 的演示几何；CubeSat 路径会传入 1U 几何 |

## 文献和工程资料索引

### 系统综述和任务边界

- He, L. et al., "Developments of attitude determination and control system for microsatellite technology," 2021, DOI [10.1177/0959651819895173](https://doi.org/10.1177/0959651819895173).
- Hu, Q. et al., "Spacecraft attitude planning and control under multiple constraints: Review and prospects," 2022, DOI [10.7527/S1000-6893.2022.27351](https://doi.org/10.7527/S1000-6893.2022.27351).
- Hasan, M.N. et al., "Fault-tolerant spacecraft attitude control: A critical assessment," 2022, DOI [10.1016/j.paerosci.2022.100806](https://doi.org/10.1016/j.paerosci.2022.100806).
- Ovchinnikov, M.Y. and Roldugin, D.S., "A survey on active magnetic attitude control algorithms for small satellites," 2019, DOI [10.1016/j.paerosci.2019.05.006](https://doi.org/10.1016/j.paerosci.2019.05.006).
- [NASA Small Spacecraft Technology State of the Art](https://www.nasa.gov/smallsat-institute/sst-soa/).
- [ECSS-E-ST-60-30C AOCS requirements](https://ecss.nl/standard/ecss-e-st-60-30c-satellite-attitude-and-orbit-control-system-aocs-requirements/).

### v0.5 刚体、高保真和执行机构升级锚点

- Wertz, J.R., *Spacecraft Attitude Determination and Control*.
- Schaub, H. and Junkins, J.L., *Analytical Mechanics of Space Systems*.
- [Basilisk 航天器和反作用轮文档](https://avslab.github.io/basilisk/).
- [NASA 42](https://github.com/ericstoneking/42).
- [GMAT Spacecraft Attitude](https://documentation.help/GMAT/SpacecraftAttitude.html).
- Lee, K.-W. et al., "A study of reaction wheel configurations for a 3-axis satellite attitude control," *Advances in Space Research*, 2010.
- Markley, F.L. et al., "Maximum Torque and Momentum Envelopes for Reaction Wheel Arrays," NASA GSFC, 2009.
- [AcubeSAT ADCS 仿真](https://github.com/AcubeSAT/adcs-simulation)，作为任务导向 ADCS 开源文档参考。
- Li, Y. 等，柔性多体航天器建模，DOI [10.2514/1.G007137](https://doi.org/10.2514/1.G007137).
- He, G. and Cao, D.，姿态-振动协同控制，DOI [10.3390/act12040167](https://doi.org/10.3390/act12040167).
- Murilo, A. 等，刚柔卫星 MPC，DOI [10.1016/j.ymssp.2020.107129](https://doi.org/10.1016/j.ymssp.2020.107129).
- Chen, Z. and Hu, Q.，反作用轮不确定性控制，DOI [10.1109/JAS.2022.105665](https://doi.org/10.1109/JAS.2022.105665).

### 环境升级锚点

- [Basilisk 面元 SRP 和面元拖曳文档](https://avslab.github.io/basilisk/).
- [Tudat 航天器宏模型文档](https://docs.tudat.space/en/latest/user-guide/state-propagation/environment-setup/creation-celestial-body-settings/spacecraft-macromodels.html).
- [NOAA IGRF](https://www.ncei.noaa.gov/products/international-geomagnetic-reference-field).
- [NASA CCMC NRLMSIS 2.1](https://ccmc.gsfc.nasa.gov/models/NRLMSIS~2.1).

## 开源项目使用边界

- 开源项目用于结构、参数基线和算法方向参考。
- 当前实现没有把外部项目的源码整段迁入。
- 当 `v0.5` 或具体任务适配从参考项目参数切到目标硬件参数时，应把该表更新为硬件数据手册、CAD 质量属性或试验辨识结果。
