"""Small PD gain tuning example using a generic optimizer."""

from satmodel import PDController, ScenarioRunner, SimulationConfig, ZeroEnvironment, build_default_system
from satmodel.optimization import PSOOptimizer


def score_pd(value):
    system = build_default_system(controller=PDController(kp=float(value[0]), kd=float(value[1])), environment=ZeroEnvironment())
    config = SimulationConfig(duration=2.0, dt=0.03, seed=3)
    metrics = ScenarioRunner(system).run(config).metrics(config.reference)
    return metrics["final_error_deg"] + 0.05 * metrics["effort_nms"]


def main():
    result = PSOOptimizer(iterations=4, swarm_size=6, seed=4).optimize(
        score_pd,
        bounds=([0.5, 0.05], [4.0, 1.5]),
    )
    print("pd tune", result.x.round(4).tolist(), round(result.score, 4))
    return result


if __name__ == "__main__":
    main()
