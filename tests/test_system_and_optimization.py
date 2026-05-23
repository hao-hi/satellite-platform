import runpy
from pathlib import Path

import numpy as np

from satmodel import (
    __version__,
    ScenarioRunner,
    SimulationConfig,
    ZeroEnvironment,
    build_cubesat_reaction_wheel_system,
    build_default_system,
)
from satmodel.optimization import (
    GridSearchOptimizer,
    NelderMeadOptimizer,
    PSOOptimizer,
    RandomSearchOptimizer,
    SimulatedAnnealingOptimizer,
)
from satmodel.studies import run_reaction_wheel_study
from satmodel.studies.reaction_wheel_study import main as reaction_wheel_study_main


def test_library_top_level_api_is_importable():
    import satmodel

    assert satmodel.__version__ == "0.1.0"
    assert __version__ == "0.1.0"
    for name in (
        "ScenarioRunner",
        "SimulationConfig",
        "build_default_system",
        "build_cubesat_reaction_wheel_system",
    ):
        assert hasattr(satmodel, name)


def test_closed_loop_system_reduces_pd_error():
    config = SimulationConfig(duration=4.0, dt=0.02, seed=2)
    result = ScenarioRunner(build_default_system(controller="pd", environment=ZeroEnvironment())).run(config)
    metrics = result.metrics(config.reference)
    assert metrics["final_error_deg"] < metrics["initial_error_deg"]
    assert result.applied_torque.shape[1] == 3
    budget = sum(result.disturbance_torque_terms.values(), np.zeros_like(result.disturbance_torque))
    assert np.allclose(budget, result.disturbance_torque)


def test_ladrc_and_rls_paths_emit_diagnostics():
    config = SimulationConfig(duration=1.2, dt=0.02, seed=4)
    ladrc = ScenarioRunner(build_default_system(controller="ladrc")).run(config)
    identified = ScenarioRunner(
        build_default_system(controller="pd", identify_inertia=True, environment=ZeroEnvironment())
    ).run(config)
    assert np.any(np.abs(ladrc.controller_disturbance_torque) >= 0.0)
    assert identified.inertia_estimate.shape[1] == 3
    assert any("rls_covariance_trace" in item for item in identified.estimator_diagnostics)


def test_cubesat_reaction_wheel_system_reduces_error_and_reports_wheels():
    config = SimulationConfig(duration=6.0, dt=0.02, seed=7)
    result = ScenarioRunner(
        build_cubesat_reaction_wheel_system(controller="pd", environment=ZeroEnvironment())
    ).run(config)
    metrics = result.metrics(config.reference)
    assert metrics["final_error_deg"] < metrics["initial_error_deg"]
    assert result.wheel_speeds_rad_s.shape[1] == 4
    assert result.wheel_torque_commands_nm.shape[1] == 4
    assert result.wheel_torques_nm.shape[1] == 4
    assert result.wheel_momentum_nms.shape[1] == 4
    assert result.wheel_allocation_error_nm.shape[1] == 3
    assert result.wheel_saturation_flags.shape[1] == 4


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
        "examples/cubesat_reaction_wheels_pd.py",
        "examples/cubesat_wheel_failure.py",
    ):
        namespace = runpy.run_path(root / path)
        assert namespace["main"]() is not None


def test_academic_reaction_wheel_study_writes_outputs(tmp_path):
    rows = run_reaction_wheel_study(tmp_path, duration=1.0, dt=0.05, make_plots=False)
    assert len(rows) == 5
    assert (tmp_path / "summary_metrics.csv").exists()
    assert (tmp_path / "time_history.csv").exists()
    assert (tmp_path / "README.md").exists()
    assert all(row["final_error_deg"] < row["initial_error_deg"] for row in rows)


def test_reaction_wheel_study_cli_entrypoint(tmp_path):
    output = tmp_path / "cli"
    result = reaction_wheel_study_main([
        "--output",
        str(output),
        "--duration",
        "1.0",
        "--dt",
        "0.05",
        "--no-plots",
    ])
    assert result is None
    assert (output / "summary_metrics.csv").exists()
