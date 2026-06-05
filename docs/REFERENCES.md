# 参考资料说明

本项目会参考成熟仿真框架、开源研究代码、教材、论文和官方模型文档。当前仓库中的实现代码由本项目重写和组织，不直接复制未审查的外部源码。

更细的公式、参数和来源映射见 [物理模型来源与参数追溯](physics/05_sources_and_parameter_traceability.md)。

## 工程框架参考

- [Basilisk](https://avslab.github.io/basilisk/)  
  主要参考其航天器本体、状态效应器、动态效应器、面元太阳光压、面元拖曳和反作用轮状态效应器的分层思想。`satmodel` 当前的“刚体本体 + 状态效应器 + 动态扰动效应器”结构与此路线一致。

- [NASA 42](https://github.com/ericstoneking/42)  
  作为远期刚体/柔性多体、硬件在环和高保真 ADCS 仿真的参考。当前项目不做 42 那样完整的多体仿真，但保留后续扩展口。

- [Tudat spacecraft macromodels](https://docs.tudat.space/en/latest/user-guide/state-propagation/environment-setup/creation-celestial-body-settings/spacecraft-macromodels.html)  
  主要参考从球形/盒体模型升级到盒体加太阳翼、面元航天器和宏模型的路线。后续气动和 SRP 面元模型会继续沿用这种思想。

- [GMAT Spacecraft Attitude](https://documentation.help/GMAT/SpacecraftAttitude.html)  
  用作刚体姿态真值模型的边界参考，说明当前刚体模型并不等同柔性或多体高保真模型。

### 对 satmodel 的具体落地映射

| 参考项目 | 成熟范式 | satmodel 采用方式 |
| --- | --- | --- |
| Basilisk | process / task / module、消息化模块、可测试仿真组件 | v0.4 引入 `RuntimeProcess`、`RuntimeTask`、`RuntimeModule`，用于多速率调度和模块执行顺序。 |
| Basilisk spacecraft | 本体、状态效应器、动态效应器分离 | 当前保持 `SpacecraftDynamics`、反作用轮状态效应器和扰动效应器分离；v0.5 扩展柔性件、面元扰动和高保真执行机构。 |
| Tudat | environment setup 与 propagation setup 分离 | v0.5 将环境配置、传播配置、dependent variables 和终止条件分层表达。 |
| Tudat macromodels | 航天器宏模型、面元几何和环境交互 | v0.5 从盒体投影面积升级到面元气动和面元 SRP。 |
| GMAT | 资源对象和 mission sequence 分离 | v0.4 引入 `MissionSequence` 和 `ModeTimeline`，把模式/参考切换放在任务层。 |
| NASA 42 | 多体、柔性和硬件在环边界 | 长期高保真方向，不进入 v0.3/v0.4 的平台骨架阶段。 |

这些参考只定义架构口径和验证方向，不意味着复制外部项目源码或引入其运行时依赖。

## 开源研究代码参考

- [`brunopinto900/attitude_control_reaction_wheels`](https://github.com/brunopinto900/attitude_control_reaction_wheels)  
  提供当前 1U CubeSat 反作用轮演示的参数基线参考，尤其是四轮金字塔构型、伪逆分配、轮速/力矩饱和、失效场景和遥测输出。

- [`AcubeSAT/adcs-simulation`](https://github.com/AcubeSAT/adcs-simulation)  
  是任务导向 ADCS 仿真的开源参考，适合学习物理架构说明、设计依据记录和任务级文档组织方式。

- [`elharirymatteo/satellite-inertia-id`](https://github.com/elharirymatteo/satellite-inertia-id)  
  可用于参考如何分离仿真、执行机构、传感器、激励和惯量辨识实验。

- [`ActiveDisturbanceRejectionControl.jl`](https://github.com/Baggepinnen/ActiveDisturbanceRejectionControl.jl)  
  用作 LADRC 带宽参数化和扩张状态观测器结构的背景参考。

## 论文与官方资料

- He et al., "Developments of attitude determination and control system for microsatellite technology," 2021, DOI [10.1177/0959651819895173](https://doi.org/10.1177/0959651819895173)。  
  用于小/微卫星 ADCS 系统背景。

- Hu et al., "Spacecraft attitude planning and control under multiple constraints: Review and prospects," 2022, DOI [10.7527/S1000-6893.2022.27351](https://doi.org/10.7527/S1000-6893.2022.27351)。  
  用于姿态规划、控制约束和未来扩展方向背景。

- Hasan et al., "Fault-tolerant spacecraft attitude control: A critical assessment," 2022, DOI [10.1016/j.paerosci.2022.100806](https://doi.org/10.1016/j.paerosci.2022.100806)。  
  用于故障容错姿态控制和执行机构失效分析背景。

- Ovchinnikov and Roldugin, "A survey on active magnetic attitude control algorithms for small satellites," 2019, DOI [10.1016/j.paerosci.2019.05.006](https://doi.org/10.1016/j.paerosci.2019.05.006)。  
  用于小卫星磁控和磁扰动背景。

- [NASA Small Spacecraft Technology State of the Art](https://www.nasa.gov/smallsat-institute/sst-soa/)  
  提供 SmallSat GNC、反作用轮、磁力矩器和执行机构选型背景。

- Lee et al., "A study of reaction wheel configurations for a 3-axis satellite attitude control," *Advances in Space Research*, 2010。  
  用于三轮/四轮构型、轮组冗余和动量管理比较。

- Markley et al., "Maximum Torque and Momentum Envelopes for Reaction Wheel Arrays," NASA GSFC, 2009。  
  将反作用轮阵列的力矩和动量能力视为单轮约束超立方体到三维空间的投影，是轮组能力包络和约束分配的重要参考。

- [ECSS-E-ST-60-30C](https://ecss.nl/standard/ecss-e-st-60-30c-satellite-attitude-and-orbit-control-system-aocs-requirements/)  
  用作 AOCS 需求、验证和术语口径参考。

- [NOAA IGRF](https://www.ncei.noaa.gov/products/international-geomagnetic-reference-field)  
  是可选地磁场高保真适配器的目标模型。

- [NASA CCMC NRLMSIS 2.1](https://ccmc.gsfc.nasa.gov/models/NRLMSIS~2.1)  
  是可选热层密度高保真适配器的目标模型。

## 使用边界

- 外部项目主要用于架构、参数基线和算法方向参考。
- 教材和论文主要用于公式口径、建模边界和升级路线。
- 官方模型文档主要用于 IGRF、NRLMSIS 等可选高保真适配器的输入输出定义。
- 当前演示参数不能直接当作飞行硬件标称值。任务级使用时，应替换为目标卫星的 CAD 质量属性、硬件数据手册、标定数据或在轨辨识结果。
