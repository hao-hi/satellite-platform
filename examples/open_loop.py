"""Open-loop rigid-body propagation."""

from satmodel import ScenarioRunner, SimulationConfig, ZeroEnvironment, build_default_system


def main():
    system = build_default_system(controller=None, environment=ZeroEnvironment())
    result = ScenarioRunner(system).run(SimulationConfig(duration=1.5, dt=0.02))
    print("open loop", result.metrics())
    return result


if __name__ == "__main__":
    main()
