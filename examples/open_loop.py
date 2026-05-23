"""开环刚体传播示例。

该脚本不启用控制器，也不加入环境扰动，用来观察初始角速度下刚体姿态的自然演化。
"""

from satmodel import ScenarioRunner, SimulationConfig, ZeroEnvironment, build_default_system


def main():
    # ZeroEnvironment 让结果只反映刚体动力学本身，便于做最小示例和调试。
    system = build_default_system(controller=None, environment=ZeroEnvironment())
    result = ScenarioRunner(system).run(SimulationConfig(duration=1.5, dt=0.02))
    # metrics() 会汇总姿态误差、力矩积分等常用标量指标。
    print("open loop", result.metrics())
    return result


if __name__ == "__main__":
    main()
