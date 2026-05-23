"""PD 参数调优示例。

用粒子群优化器搜索 `kp` 和 `kd`，目标函数同时惩罚末端误差和控制力矩消耗。
"""

from satmodel import PDController, ScenarioRunner, SimulationConfig, ZeroEnvironment, build_default_system
from satmodel.optimization import PSOOptimizer


def score_pd(value):
    # 优化器传入二维参数向量：value[0] 是 kp，value[1] 是 kd。
    system = build_default_system(controller=PDController(kp=float(value[0]), kd=float(value[1])), environment=ZeroEnvironment())
    config = SimulationConfig(duration=2.0, dt=0.03, seed=3)
    metrics = ScenarioRunner(system).run(config).metrics(config.reference)
    # 分数越小越好：末端误差为主，力矩作用积分作为温和正则项。
    return metrics["final_error_deg"] + 0.05 * metrics["effort_nms"]


def main():
    # 迭代次数和粒子数刻意设小，保证示例运行快；任务级调参可适当增大。
    result = PSOOptimizer(iterations=4, swarm_size=6, seed=4).optimize(
        score_pd,
        bounds=([0.5, 0.05], [4.0, 1.5]),
    )
    print("pd tune", result.x.round(4).tolist(), round(result.score, 4))
    return result


if __name__ == "__main__":
    main()
