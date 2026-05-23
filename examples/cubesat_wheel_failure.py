"""1U CubeSat 反作用轮失效冒烟示例。

禁用四轮金字塔中的一个轮，快速检查剩余三轮是否还能维持基本三轴控制。
"""

from satmodel import ScenarioRunner, SimulationConfig, ZeroEnvironment, build_cubesat_reaction_wheel_system


def main():
    system = build_cubesat_reaction_wheel_system(controller="pd", environment=ZeroEnvironment())
    # 禁用第 0 个轮后，分配器会基于剩余启用轮实时重新计算可实现力矩。
    system.actuator.disable_wheel(0)
    config = SimulationConfig(duration=2.0, dt=0.02, seed=9)
    result = ScenarioRunner(system).run(config)
    print("cubesat wheel failure", result.metrics(config.reference))
    # 最后一帧 enabled 标志可确认禁用状态确实进入了遥测链路。
    print("wheel enabled flags", result.actuator_telemetry[-1].enabled.astype(int).tolist())
    return result


if __name__ == "__main__":
    main()
