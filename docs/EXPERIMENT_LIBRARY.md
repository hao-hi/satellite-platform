# satmodel 实验库说明

## 1. 实验库的作用

实验库不是一堆可运行 JSON 的集合，而是平台中的标准实验入口。

它要解决四件事：

1. 告诉用户当前有哪些成熟实验可直接复用。
2. 告诉用户每个实验在回答什么问题。
3. 告诉用户这一组实验应该先看哪些指标和图。
4. 告诉用户跑完之后下一组实验该接到哪里。

因此，实验库采用“实验主线 + 代表实验 + 代表资产”的组织方式，而不是按文件名堆列表。

其中“代表资产”建议长期区分为两类：

- 代表计划：当前主线最适合继续复用和派生的计划
- 代表结果：当前主线最适合展示、读图和汇报的稳定结果

在平台界面中，这组关系应始终通过统一的主线资产链表达：

`代表模板 -> 代表计划 -> 代表结果 -> 下一步实验`

“最新结果”不等于“代表结果”。最新结果主要用于回看刚跑完的一次实验，代表结果则应优先保持讲解口径和展示入口稳定。

## 2. 当前实验主线

建议按下面顺序推进实验，而不是随机挑选：

| 阶段 | 目标 | 代表实验 |
| --- | --- | --- |
| 闭环基线 | 先确认最小姿态保持闭环能稳定运行 | `quick_pd_zero` / `quick_pd_showcase` |
| 控制器比较 | 在统一场景下比较控制律差异 | `quick_controller_benchmark_compare` |
| 统计鲁棒性 | 检查随机性和扰动下的稳定性 | `quick_pd_seed_mc` |
| 环境与扰动 | 检查轨道环境与扰动项是否显著 | `quick_environment_compare` / `cubesat_rw_disturbance_breakdown` |
| 任务模式 | 检查任务切换和参考目标切换过程 | `cubesat_rw_sun_transition_curated` / `cubesat_rw_earth_transition_curated` |
| 执行器边界 | 检查反作用轮能力、饱和和故障边界 | `cubesat_rw_wheel_capability` / `cubesat_rw_fault_gain_tradeoff` |
| 验收收口 | 把“能跑”转成“是否通过验收” | `quick_pd_acceptance_gate` |

这条主线对应平台里的标准研究节奏：

`先闭环 -> 再比较 -> 再看统计边界 -> 再看环境与任务 -> 最后收口到执行器和验收`

主线之外，建议明确保留少量“关键支线”，但不要把它们混成新的主线阶段：

| 支线 | 从哪一步分出 | 代表实验 | 作用 |
| --- | --- | --- | --- |
| 感知链支线 | 统计鲁棒性 | `quick_sensor_noise_sensitivity` | 把噪声与测量质量从总鲁棒性里拆出来，单独判断感知链退化影响 |
| 故障鲁棒性支线 | 统计鲁棒性 | `cubesat_rw_fault_seed_mc` | 把 Monte Carlo 推到单轮故障场景，单独观察最差工况和稳定边界 |
| 动量管理支线 | 执行器边界 | `cubesat_rw_momentum_management_sweep` | 在“力矩够不够”之后继续回答“轮速怎么管更合适” |

平台界面里应坚持一个约束：

- 主线始终保持 7 阶段，方便讲清楚平台研究节奏
- 支线作为某一阶段的深化入口出现，方便补充细化实验

## 3. 当前代表实验

### 3.1 闭环基线

| 实验 | 作用 | 建议看什么 |
| --- | --- | --- |
| `quick_pd_zero` | 最小姿态保持闭环 | `final_error_deg`、`rms_error_deg`、姿态误差曲线 |
| `quick_pd_showcase` | 首次平台演示入口 | 摘要卡、误差图、回放入口 |

### 3.2 控制器比较

| 实验 | 作用 | 建议看什么 |
| --- | --- | --- |
| `quick_controller_benchmark_compare` | 比较 `PD` 与 `LADRC` | 最佳 run、误差曲线、峰值力矩 |
| `quick_controller_benchmark_orbital_compare` | 在轨道环境下比较控制器 | 环境变化后的误差和力矩差异 |

### 3.3 参数整定

| 实验 | 作用 | 建议看什么 |
| --- | --- | --- |
| `quick_pd_gain_sweep` | 比较比例增益变化 | 收敛速度、末端误差、峰值力矩 |
| `quick_pd_damping_sweep` | 比较阻尼增益变化 | 振荡抑制、误差平滑性、控制动作 |

### 3.4 鲁棒性与测量敏感性

| 实验 | 作用 | 建议看什么 |
| --- | --- | --- |
| `quick_pd_seed_mc` | 做轻量 Monte Carlo | 通过率、最差 run、误差分布 |
| `quick_sensor_noise_sensitivity` | 比较测量质量退化 | 噪声档位、误差增长、通过率变化 |
| `cubesat_rw_fault_seed_mc` | 在反作用轮故障场景做 Monte Carlo | 故障后稳定边界、最差工况 |

### 3.5 环境与扰动

| 实验 | 作用 | 建议看什么 |
| --- | --- | --- |
| `quick_environment_compare` | 比较 zero / orbital 环境 | 误差差异、控制力矩差异、扰动力矩变化 |
| `cubesat_rw_disturbance_breakdown` | 比较主导扰动项 | 各扰动峰值、主导扰动、边界变化 |
| `cubesat_rw_disturbance_capability_tradeoff` | 比较扰动与执行器耦合 | 扰动模板、轮组能力、通过率变化 |

### 3.6 任务模式

| 实验 | 作用 | 建议看什么 |
| --- | --- | --- |
| `cubesat_rw_sun_transition_curated` | `detumble -> sun_pointing` | 过渡误差峰值、任务时间线、姿态回放 |
| `cubesat_rw_earth_transition_curated` | `detumble -> earth_pointing` | 对地参考、过渡误差、回放一致性 |
| `cubesat_rw_earth_transition_wheel_capability` | 任务模式与执行器能力耦合 | 模式切换过程中的力矩与饱和 |

### 3.7 执行器边界

| 实验 | 作用 | 建议看什么 |
| --- | --- | --- |
| `cubesat_rw_wheel_capability` | 比较轮组最大力矩 | 收敛速度、峰值力矩、轮速趋势 |
| `cubesat_rw_fault_gain_tradeoff` | 比较故障后增益取舍 | 误差改善与控制边界的平衡 |
| `cubesat_rw_momentum_management_sweep` | 比较轮速回拉策略 | 轮速、姿态误差、执行器余量 |

### 3.8 验收收口

| 实验 | 作用 | 建议看什么 |
| --- | --- | --- |
| `quick_pd_acceptance_gate` | 把结果转成通过/失败判断 | 失败原因、门限差异、代表结果 |

## 4. 首次展示推荐路径

如果是第一次给老师、同事或评审展示平台，不建议把所有实验都讲一遍。更适合按下面 5 步走：

1. `quick_pd_showcase`
2. `controller_benchmark_showcase`
3. `orbital_environment_showcase`
4. `sun_transition_showcase`
5. `fault_wheel_showcase`

这条路径覆盖：

- 最小闭环
- 控制器比较
- 环境影响
- 任务模式切换
- 执行器边界

它比随机打开结果更稳定，也更接近成熟仿真平台中的固定 demo 路线。

如果主线已经讲清楚，需要继续深入某一步，建议再补三类“关键支线演示”：

1. `sensor_quality_showcase`：从统计鲁棒性继续拆到感知链
2. `disturbance_breakdown_showcase`：从环境差异继续拆到主导扰动项
3. `earth_transition_showcase`：从太阳指向任务继续扩到对地任务

这样平台展示时能保持“先主线、再深入”的节奏，而不是一开始就把所有实验平铺出来。

## 5. 创建实验时的推荐模板

当前创建器中最重要的几类模板如下：

| 模板 | 适合用途 | 典型变量 |
| --- | --- | --- |
| `PD 参数整定` | 做第一轮闭环整定 | `controller.pd_kp`、`controller.pd_kd` |
| `控制器基准对比` | 比较不同控制器 | `system.controller` |
| `随机鲁棒性` | 做 Monte Carlo | `time.seed` + `monte_carlo` |
| `环境敏感性` | 比较环境变化 | `system.environment` |
| `环境扰动分解` | 比较主导扰动 | `system.disturbance_profile` |
| `太阳指向切换` | 做模式切换演示 | `controller.pd_kd` + `detumble_then_hold` |
| `对地指向切换` | 做对地任务演示 | `controller.pd_kd` + `detumble_then_hold` |
| `执行器能力对比` | 比较轮组能力 | `actuators.reaction_wheels.max_torque_nm` |
| `轮速与动量管理` | 比较轮速回拉策略 | `actuators.reaction_wheels.momentum_gain` |
| `严格验收门限` | 收紧验收标准 | `controller.pd_kp` + 严格门限 |

创建器中的模板不应只给出变量名，还应同步给出：

- 这个模板在回答什么研究问题
- 为什么推荐扫描这个变量
- 典型推荐取值或推荐取值模板
- 建议搭配的任务模板和任务模式
- 跑完后优先看哪一组结果图包

在界面上，这些信息最好收敛为固定四张卡：

1. `标准实验协议卡`
2. `变量设计任务书`
3. `任务与验收任务书`
4. `结果阅读任务书`

这样用户看到的不是零散字段，而是一条从实验问题到结果阅读的连续设计链。

## 6. 每个实验至少应该说明什么

一个成熟实验不应只有计划名和 JSON 路径，至少应明确：

1. 研究问题
2. 场景基线
3. 扫描变量
4. 任务模板
5. 验收口径
6. 推荐结果图
7. 下一步实验

这也是平台界面里“实验库 -> 创建实验 -> 结果总览”要共用的一套语义。

## 7. 推荐结果产物

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

如果实验包含任务模式或运行时描述，建议还包含：

- `mode_timeline.json`
- `runtime_schedule.json`

平台界面里的实验详情还应固定回答“结果怎么读”：

1. 先看哪张摘要或哪类图
2. 是否应优先去结果对比
3. 是否应优先去姿态回放
4. 报告里最应该写哪类结论
5. 下一步实验该接到哪里

## 8. 实验库后续扩展方向

后续实验迭代建议继续围绕下面三类推进：

### 平台层优先

- 更稳定的代表计划
- 更稳定的代表结果
- 更清楚的实验主线映射

### 结果层增强

- 轮速图
- 扰动力矩预算图
- 最差 run 自动解释
- 模式切换高亮图

### 物理层后续

- 更高保真环境模型
- 更高保真执行机构模型
- 更高保真传感器模型

顺序上，仍应先把实验工作流做稳，再继续增加高保真模型。
