# 05 来源与参数追溯

## 使用规则

本项目把物理输入分成三类：

1. **文献/官方资料给定**：可直接追溯到论文、标准、官方文档或教材。
2. **参考项目给定**：用于首版演示的社区项目参数，需在后续任务中替换为目标硬件数据。
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
| 重力梯度 | `3 mu/r^3 rhat x I rhat` | Wertz 环境扰动力矩模型 |
| 残余磁矩 | `m_res x B` | 小卫星磁控/磁扰动模型；Ovchinnikov and Roldugin 2019 |
| 当前拖曳 | `-0.5 rho Cd A |v| v` | 一阶工程扰动模型 |
| 当前 SRP | `-P C_R A s_hat` | 一阶工程扰动模型 |

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
| 气动/SRP 压心偏置 | 当前扰动效应器配置值 | `satmodel` 工程假设 | 用于产生非零力矩 |
| 残余磁偶极矩 | 当前残余磁扰动效应器配置值 | `satmodel` 工程假设 | 用于产生非零磁扰动 |

## 文献和工程资料索引

### 系统综述和任务边界

- He, L. et al., "Developments of attitude determination and control system for microsatellite technology," 2021, DOI [10.1177/0959651819895173](https://doi.org/10.1177/0959651819895173).
- Hu, Q. et al., "Spacecraft attitude planning and control under multiple constraints: Review and prospects," 2022, DOI [10.7527/S1000-6893.2022.27351](https://doi.org/10.7527/S1000-6893.2022.27351).
- Hasan, M.N. et al., "Fault-tolerant spacecraft attitude control: A critical assessment," 2022, DOI [10.1016/j.paerosci.2022.100806](https://doi.org/10.1016/j.paerosci.2022.100806).
- Ovchinnikov, M.Y. and Roldugin, D.S., "A survey on active magnetic attitude control algorithms for small satellites," 2019, DOI [10.1016/j.paerosci.2019.05.006](https://doi.org/10.1016/j.paerosci.2019.05.006).
- [NASA Small Spacecraft Technology State of the Art](https://www.nasa.gov/smallsat-institute/sst-soa/).
- [ECSS-E-ST-60-30C AOCS requirements](https://ecss.nl/standard/ecss-e-st-60-30c-satellite-attitude-and-orbit-control-system-aocs-requirements/).

### 刚体、高保真和执行机构升级锚点

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
- 当后续从参考项目参数切到目标硬件参数时，应把该表更新为硬件数据手册、CAD 质量属性或试验辨识结果。
