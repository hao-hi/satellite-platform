# satmodel 实验库建议

本文档把当前平台阶段最值得优先建设的实验内容整理成一份可执行清单，目标是让 `satmodel` 的实验资产先变得清楚、可复现、可展示，再继续往高保真和产品化扩。

## 1. 当前实验建设原则

优先做下面四类实验：

1. 控制器整定实验  
   目标：比较不同控制参数对收敛速度、稳态误差和控制力矩的影响。
2. 鲁棒性实验  
   目标：比较随机种子、噪声和初值变化对稳定性的影响。
3. 任务模式切换实验  
   目标：比较 detumble、惯性保持、对日和对地等模式切换时的姿态过渡表现。
4. 执行器能力边界实验  
   目标：比较反作用轮力矩、饱和和能力限制对控制性能的影响。

在这四类之外，当前平台已经值得补一条单独的“环境扰动分解实验”支线，用来回答“环境会不会影响结果”之后的下一层问题：究竟是哪类扰动最值得优先建模和关注。

当前创建实验工作台也已经适合支持一部分二维权衡实验，例如“扰动模板 × 轮组最大力矩”。这类实验适合放在完成单变量扫描之后，用来回答“主导扰动更关键，还是执行器能力更关键”。

当前平台结果页也应把实验放回标准主线里解释，而不是只给一张指标表。理想状态下，用户跑完一次实验后，可以直接看到“当前所处环节”和“推荐下一组实验”，这样实验库、创建实验和结果报告才真正连成闭环。

进一步往成熟平台靠时，结果页还应明确告诉用户“这组实验优先看哪几张图、每张图在回答什么问题”。也就是说，实验库里给出推荐图表，结果页里给出阅读顺序，这两者要形成一致的实验叙事。

当前阶段不建议一开始就把主要精力放在复杂故障树、数据库或高保真外场堆叠上，因为这些内容如果缺少一套稳定实验工作流，会很快变得难以复查和难以展示。

## 2. 推荐实验清单

### 2.1 PD 参数整定

- 基线场景：`quick_pd_zero`
- 推荐扫描变量：`controller.pd_kp`、`controller.pd_kd`
- 关注指标：
  - `final_error_deg`
  - `rms_error_deg`
  - `peak_torque_nm`
- 适合用途：
  - 快速演示平台闭环
  - 比较控制器参数差异
  - 给后续验收阈值找一个合理起点

### 2.2 Monte Carlo 鲁棒性

- 基线场景：`quick_pd_zero` 或 `cubesat_rw_fault`
- 推荐扫描变量：`time.seed`
- 推荐 Monte Carlo：
  - `samples = 8 ~ 20`
  - 固定起始 seed，保证可复现
- 关注指标：
  - 通过率
  - 最佳/最差 run 差距
  - 姿态误差分布

### 2.3 任务模式切换

- 基线场景：`cubesat_rw_fault`
- 推荐 mission：
  - `single_mode`
  - `detumble_then_hold`
- 推荐模式：
  - `inertial_hold`
  - `sun_pointing`
  - `earth_pointing`
- 关注指标：
  - 模式切换前后误差峰值
  - 切换时间段内控制力矩变化
  - mode timeline 与姿态回放的一致性

### 2.4 执行器能力对比

- 基线场景：`cubesat_rw_fault`
- 推荐扫描变量：`actuators.reaction_wheels.max_torque_nm`
- 关注指标：
  - 收敛速度
  - 峰值力矩
  - 饱和标志
  - 轮速变化趋势

### 2.5 验收门限实验

- 目标：把“能跑”变成“可验收”
- 推荐门限：
  - `max_final_error_deg`
  - `max_rms_error_deg`
  - `max_peak_torque_nm`
- 适合用途：
  - 给结果报告加上通过/失败判断
  - 形成后续 dashboard 的状态筛选基础

## 3. 推荐实验目录与命名

建议每个实验计划都具备：

- 清晰的计划名
- 明确的扫描变量说明
- 明确的实验假设
- 适用前提与对照边界
- 建议展示图表
- 结果输出目录
- 验收门限
- 场景说明或实验说明

推荐命名风格：

- `quick_pd_gain_sweep`
- `quick_pd_damping_sweep`
- `quick_pd_seed_mc`
- `quick_controller_benchmark_compare`
- `quick_environment_compare`
- `quick_sensor_noise_sensitivity`
- `cubesat_rw_disturbance_breakdown`
- `cubesat_rw_fault_seed_mc`
- `cubesat_rw_sun_transition`
- `cubesat_rw_fault_gain_tradeoff`
- `cubesat_rw_wheel_capability`
- `cubesat_rw_momentum_management_sweep`

## 3.1 当前推荐实验资产

当前平台已经适合固化为标准实验库的计划包括：

1. `quick_pd_gain_sweep`  
   作用：快速比较比例增益变化对收敛速度、误差和峰值力矩的影响。  
   适合：第一次整定控制器，或者给验收门限找合理起点。
2. `quick_pd_damping_sweep`  
   作用：比较微分增益变化对振荡抑制、误差平滑性和控制动作的影响。  
   适合：比例增益大致稳定后，继续做阻尼与平滑性整定。
3. `quick_pd_seed_mc`  
   作用：在简单基线场景下做轻量 Monte Carlo，观察通过率和最差工况。  
   适合：从“能跑”过渡到“稳不稳”的第一轮鲁棒性检查。
4. `quick_controller_benchmark_compare`  
   作用：在统一姿态保持场景里直接比较 PD 与 LADRC，形成平台第一版控制器 benchmark。  
   适合：决定当前默认控制器基线，或者给后续控制律扩展建立统一比较入口。
5. `quick_controller_benchmark_orbital_compare`  
   作用：把控制器 benchmark 从理想环境推进到轻量轨道环境，比较 PD 与 LADRC 在更真实扰动背景下的差异。  
   适合：判断控制器优劣顺序在环境变化后是否仍稳定，避免 benchmark 只停留在 zero 环境。
6. `quick_sensor_noise_sensitivity`  
   作用：比较陀螺测量噪声变化对姿态误差、通过率和最差工况的影响。  
   适合：把“鲁棒性”进一步细化为“测量质量敏感性”评估。
7. `quick_environment_compare`  
   作用：比较 ideal zero 环境与 orbital 环境下，姿态误差、控制力矩和扰动力矩预算的差异。  
   适合：把平台从“理想闭环演示”推进到“环境扰动是否重要”的第一轮工程判断。
8. `quick_pd_acceptance_gate`  
   作用：在同一 PD 基线下使用严格验收门限，比较哪些参数点只是勉强可用，哪些参数点真正稳健。  
   适合：在完成基础整定后收紧平台验收口径，让“通过/失败”更有区分度。
9. `cubesat_rw_fault_seed_mc`  
   作用：在单轮失效和轨道环境下做 Monte Carlo，观察故障闭环的稳定边界。  
   适合：把鲁棒性实验从基线场景推进到更真实的在轨执行器场景。
10. `cubesat_rw_sun_transition_curated`  
   作用：围绕 `detumble -> sun_pointing` 任务流程，检查模式切换过程。  
   适合：演示任务模式切换、时间线联动和姿态回放。
11. `cubesat_rw_fault_gain_tradeoff`  
   作用：比较故障后提高比例增益到底是在改善误差，还是更快逼近控制边界。  
   适合：做失效后的参数重整定与保守/激进控制权衡。
12. `cubesat_rw_wheel_capability`  
   作用：比较执行器力矩能力变化对控制性能、饱和风险和轮速趋势的影响。  
   适合：做工程边界判断，回答“这套执行器能力够不够”。
13. `cubesat_rw_momentum_management_sweep`  
   作用：比较轮组动量管理增益对轮速回拉、姿态误差和执行器余量的影响。  
   适合：从“执行器能力够不够”继续推进到“轮速怎么管更合适”的工程问题。
14. `cubesat_rw_disturbance_breakdown`  
   作用：在轨道 CubeSat 基线场景中逐项打开重力梯度、残余磁矩、气动、太阳压及其共同作用，比较主导扰动项。  
   适合：把环境实验从“零扰动对轨道”继续推进到“具体是哪类扰动最主导当前误差与预算”。
15. `cubesat_rw_disturbance_capability_tradeoff`  
   作用：同时扫描主导扰动模板和反作用轮最大力矩，比较外部环境与执行器能力的耦合边界。  
   适合：在完成扰动分解后，继续判断“主导扰动已经足够大，还是执行器能力已经不够”。

## 4. 推荐结果产物

每个实验根目录至少应包含：

- `README.md`
- `index.json`
- `summary_metrics.csv`
- `experiment_manifest.json`
- `dashboard.html`

每个 run 至少应包含：

- `manifest.json`
- `metrics.csv`
- `time_history.csv`
- `events.csv`

## 5. 当前最适合扩展的插件位

这里的“插件”指平台中的可插拔扩展点，不是强绑定第三方依赖。

### 5.1 实验模板扩展

- 位置：实验创建器模板
- 适合新增：
  - 环境敏感性实验
  - 环境扰动分解实验
  - 控制器对比实验
  - 轮速管理实验
  - 传感器质量敏感性实验

### 5.2 结果报告扩展

- 位置：`ReportBuilder`
- 适合新增：
  - 轮速图
  - 环境扰动分解图
  - 扰动力矩预算图
  - 验收摘要卡
  - 最佳/最差 run 自动点评

### 5.3 runtime / mission 扩展

- 位置：runtime 模板与 mission 模板
- 适合新增：
  - 多速率控制链
  - 传感器异步采样
  - 模式切换事件
  - 安全模式回退流程

### 5.4 dashboard 面板扩展

- 位置：`dashboard.html`
- 适合新增：
  - run 分布图
  - 时间线高亮
  - 轮组遥测图
  - 任务步骤说明栏

## 6. 推荐实施顺序

建议按下面顺序推进：

1. 先补齐实验模板与实验说明。
2. 再补齐验收门限和结果报告。
3. 然后扩 mode timeline、runtime schedule 和回放联动。
4. 最后再进入高保真模型、数据库和更复杂插件。

这样做的好处是：每一步都能立刻展示、立刻运行、立刻复查，而不是做出一堆结构但没有真正可讲述的实验内容。

当前平台界面中的“创建实验”工作台也按这个顺序收敛成四步：

1. 研究问题  
2. 场景与变量  
3. 任务与验收  
4. 预览与生成
