"""PD 姿态稳定示例。

使用库的高层构造器装配“刚体 + 理想力矩执行器 + PD 控制器”，适合作为闭环入门脚本。
"""

import argparse

from satmodel import ScenarioRunner, SimulationConfig, ZeroEnvironment, build_default_system
from satmodel.plotting import plot_result


def main(plot: bool = False):
    # 这里使用零环境，控制效果不会被气动、磁扰动或太阳光压影响。
    system = build_default_system(controller="pd", environment=ZeroEnvironment())
    config = SimulationConfig(duration=5.0, dt=0.02)
    result = ScenarioRunner(system).run(config)
    print("pd", result.metrics(config.reference))
    if plot:
        import matplotlib.pyplot as plt

        # plot_result 会画姿态误差、角速度、控制力矩等默认诊断曲线。
        plot_result(result, config.reference)
        plt.show()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--plot", action="store_true")
    main(plot=parser.parse_args().plot)
