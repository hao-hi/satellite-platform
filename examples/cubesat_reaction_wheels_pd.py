"""1U CubeSat 反作用轮 PD 闭环示例。

该脚本使用四轮金字塔轮组，而不是理想体轴力矩执行器，因此会输出轮速、分配误差和饱和统计。
"""

import argparse

from satmodel import ScenarioRunner, SimulationConfig, ZeroEnvironment, build_cubesat_reaction_wheel_system
from satmodel.plotting import plot_result


def main(plot: bool = False):
    # ZeroEnvironment 让这次闭环主要展示控制器和反作用轮轮组的交互。
    system = build_cubesat_reaction_wheel_system(controller="pd", environment=ZeroEnvironment())
    config = SimulationConfig(duration=6.0, dt=0.02, seed=7)
    result = ScenarioRunner(system).run(config)
    metrics = result.metrics(config.reference)
    print("cubesat reaction wheels pd", metrics)
    # 这些诊断量对应轮组模型的关键健康指标：轮速峰值、分配残差和饱和次数。
    print("wheel speed peak rad/s", abs(result.wheel_speeds_rad_s).max())
    print("wheel allocation error peak N m", abs(result.wheel_allocation_error_nm).max())
    print("wheel saturation count", int(result.wheel_saturation_flags.sum()))
    if plot:
        import matplotlib.pyplot as plt

        plot_result(result, config.reference)
        # 额外画轮速曲线，方便判断是否逼近速度上限或出现明显动量积累。
        plt.figure(figsize=(8, 3), constrained_layout=True)
        plt.plot(result.time, result.wheel_speeds_rad_s)
        plt.xlabel("time s")
        plt.ylabel("wheel speed rad/s")
        plt.grid(True, alpha=0.3)
        plt.show()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--plot", action="store_true")
    main(plot=parser.parse_args().plot)
