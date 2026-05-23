"""Short reaction-wheel failure smoke scenario for the 1U CubeSat baseline."""

from satmodel import ScenarioRunner, SimulationConfig, ZeroEnvironment, build_cubesat_reaction_wheel_system


def main():
    system = build_cubesat_reaction_wheel_system(controller="pd", environment=ZeroEnvironment())
    system.actuator.disable_wheel(0)
    config = SimulationConfig(duration=2.0, dt=0.02, seed=9)
    result = ScenarioRunner(system).run(config)
    print("cubesat wheel failure", result.metrics(config.reference))
    print("wheel enabled flags", result.actuator_telemetry[-1].enabled.astype(int).tolist())
    return result


if __name__ == "__main__":
    main()
