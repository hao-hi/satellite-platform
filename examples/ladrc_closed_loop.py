"""LADRC 姿态稳定示例。

LADRC 会在控制器内部估计等效扰动；这里故意加入常值外扰，方便查看其诊断量。
"""

import argparse

from satmodel import ScenarioRunner, SimulationConfig, build_default_system
from satmodel.plotting import plot_result


def main(plot: bool = False):
    system = build_default_system(controller="ladrc")
    # extra_disturbance 是施加在本体系的固定外部力矩，用来测试抗扰能力。
    config = SimulationConfig(
        duration=5.0,
        dt=0.02,
        extra_disturbance=[8e-4, -6e-4, 5e-4],
    )
    result = ScenarioRunner(system).run(config)
    print("ladrc", result.metrics(config.reference))
    if plot:
        import matplotlib.pyplot as plt

        # 图中包含控制器扰动估计，可用于检查 LADRC 的内部观测趋势。
        plot_result(result, config.reference)
        plt.show()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--plot", action="store_true")
    main(plot=parser.parse_args().plot)
