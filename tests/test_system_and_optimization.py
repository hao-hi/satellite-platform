import runpy
from pathlib import Path

import numpy as np

from satmodel import ScenarioRunner, SimulationConfig, ZeroEnvironment, build_default_system
from satmodel.optimization import (
    GridSearchOptimizer,
    NelderMeadOptimizer,
    PSOOptimizer,
    RandomSearchOptimizer,
    SimulatedAnnealingOptimizer,
)


def test_closed_loop_system_reduces_pd_error():
    config = SimulationConfig(duration=4.0, dt=0.02, seed=2)
    result = ScenarioRunner(build_default_system(controller="pd", environment=ZeroEnvironment())).run(config)
    metrics = result.metrics(config.reference)
    assert metrics["final_error_deg"] < metrics["initial_error_deg"]
    assert result.applied_torque.shape[1] == 3


def test_ladrc_and_rls_paths_emit_diagnostics():
    config = SimulationConfig(duration=1.2, dt=0.02, seed=4)
    ladrc = ScenarioRunner(build_default_system(controller="ladrc")).run(config)
    identified = ScenarioRunner(
        build_default_system(controller="pd", identify_inertia=True, environment=ZeroEnvironment())
    ).run(config)
    assert np.any(np.abs(ladrc.controller_disturbance_torque) >= 0.0)
    assert identified.inertia_estimate.shape[1] == 3
    assert any("rls_covariance_trace" in item for item in identified.estimator_diagnostics)


def test_optimizers_smoke_on_convex_objective():
    objective = lambda value: float(np.sum((np.asarray(value) - np.array([0.25, -0.4])) ** 2))
    bounds = ([-1.0, -1.0], [1.0, 1.0])
    for optimizer in (
        GridSearchOptimizer(points_per_dim=4),
        RandomSearchOptimizer(iterations=5, seed=1),
        NelderMeadOptimizer(iterations=5),
        SimulatedAnnealingOptimizer(iterations=5, seed=2),
        PSOOptimizer(iterations=5, swarm_size=5, seed=3),
    ):
        result = optimizer.optimize(objective, bounds)
        assert np.isfinite(result.score)
        assert result.x.shape == (2,)


def test_examples_smoke():
    root = Path(__file__).resolve().parents[1]
    for path in (
        "examples/open_loop.py",
        "examples/pd_closed_loop.py",
        "examples/ladrc_closed_loop.py",
        "examples/mekf_rls_identification.py",
        "examples/tune_pd.py",
    ):
        namespace = runpy.run_path(root / path)
        assert namespace["main"]() is not None
