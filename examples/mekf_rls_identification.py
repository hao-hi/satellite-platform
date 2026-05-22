"""MEKF and diagonal RLS identification example."""

import argparse

import numpy as np

from satmodel import ScenarioRunner, SimulationConfig, ZeroEnvironment, build_default_system
from satmodel.plotting import plot_result


def main(plot: bool = False):
    system = build_default_system(controller="pd", identify_inertia=True, environment=ZeroEnvironment())
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
