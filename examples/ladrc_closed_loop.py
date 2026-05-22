"""LADRC attitude stabilization with internal disturbance diagnostics."""

import argparse

from satmodel import ScenarioRunner, SimulationConfig, build_default_system
from satmodel.plotting import plot_result


def main(plot: bool = False):
    system = build_default_system(controller="ladrc")
    config = SimulationConfig(
        duration=5.0,
        dt=0.02,
        extra_disturbance=[8e-4, -6e-4, 5e-4],
    )
    result = ScenarioRunner(system).run(config)
    print("ladrc", result.metrics(config.reference))
    if plot:
        import matplotlib.pyplot as plt

        plot_result(result, config.reference)
        plt.show()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--plot", action="store_true")
    main(plot=parser.parse_args().plot)
