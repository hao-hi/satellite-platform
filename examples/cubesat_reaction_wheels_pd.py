"""PD stabilization of the rigid 1U CubeSat reaction-wheel baseline."""

import argparse

from satmodel import ScenarioRunner, SimulationConfig, ZeroEnvironment, build_cubesat_reaction_wheel_system
from satmodel.plotting import plot_result


def main(plot: bool = False):
    system = build_cubesat_reaction_wheel_system(controller="pd", environment=ZeroEnvironment())
    config = SimulationConfig(duration=6.0, dt=0.02, seed=7)
    result = ScenarioRunner(system).run(config)
    metrics = result.metrics(config.reference)
    print("cubesat reaction wheels pd", metrics)
    print("wheel speed peak rad/s", abs(result.wheel_speeds_rad_s).max())
    print("wheel allocation error peak N m", abs(result.wheel_allocation_error_nm).max())
    print("wheel saturation count", int(result.wheel_saturation_flags.sum()))
    if plot:
        import matplotlib.pyplot as plt

        plot_result(result, config.reference)
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
