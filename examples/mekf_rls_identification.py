"""MEKF 与对角惯量 RLS 辨识示例。

示例展示估计器链路如何同时处理姿态估计和简化惯量辨识，适合检查诊断输出。
"""

import argparse

import numpy as np

from satmodel import ScenarioRunner, SimulationConfig, ZeroEnvironment, build_default_system
from satmodel.plotting import plot_result


def main(plot: bool = False):
    # identify_inertia=True 会启用对角惯量 RLS；零环境让辨识输入更容易解释。
    system = build_default_system(controller="pd", identify_inertia=True, environment=ZeroEnvironment())
    # disturbance_noise_std 给扰动力矩重构留一点噪声，模拟更接近真实遥测的条件。
    config = SimulationConfig(duration=6.0, dt=0.02, disturbance_noise_std=1e-5)
    result = ScenarioRunner(system).run(config)
    print("rls final J", np.round(result.inertia_estimate[-1], 6).tolist())
    if plot:
        import matplotlib.pyplot as plt

        plot_result(result, config.reference)
        plt.show()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--plot", action="store_true")
    main(plot=parser.parse_args().plot)
