"""Lightweight v0.2 platform workflow tests."""

import csv
import json

import pytest

from satmodel import (
    ExperimentRunner,
    PlatformProject,
    MonteCarlo,
    ScenarioRunner,
    ScenarioSpec,
    StudyRunner,
    Sweep,
    compile_scenario,
    load_scenario,
    experiment_plan_from_mapping,
    scenario_from_mapping,
)
from satmodel.cli import run_experiment_main
from satmodel.cli import run_scenario_main
from satmodel.cli import validate_experiment_main
from satmodel.cli import validate_scenario_main
from satmodel.cli import build_dashboard_main
from satmodel.platform.webapp import (
    create_workspace_experiment_plan,
    discover_workspace,
    run_workspace_experiment,
    validate_workspace_experiment,
)


def _scenario_mapping(output_root):
    return {
        "schema_version": 1,
        "metadata": {
            "name": "platform_smoke",
            "description": "Short platform workflow smoke test",
        },
        "time": {
            "duration_s": 0.4,
            "dt_s": 0.05,
            "seed": 5,
        },
        "system": {
            "builder": "default",
            "controller": "pd",
            "identify_inertia": False,
            "environment": "zero",
        },
        "initial_state": {
            "use_default": True,
        },
        "outputs": {
            "root": str(output_root),
            "save_manifest_json": True,
            "save_metrics_csv": True,
            "save_time_history_csv": True,
            "save_markdown_report": True,
        },
    }


def test_scenario_spec_compiles_to_existing_runner(tmp_path):
    spec = scenario_from_mapping(_scenario_mapping(tmp_path / "single"))
    compiled = compile_scenario(spec)
    result = ScenarioRunner(compiled.system).run(compiled.config)
    metrics = result.metrics(compiled.config.reference)
    assert isinstance(spec, ScenarioSpec)
    assert result.time.size == 8
    assert metrics["final_error_deg"] >= 0.0


def test_scenario_compiler_applies_controller_parameters(tmp_path):
    mapping = _scenario_mapping(tmp_path / "controller")
    mapping["controller"] = {"pd_kp": 0.25, "pd_kd": 0.075}
    spec = scenario_from_mapping(mapping)
    compiled = compile_scenario(spec)

    assert compiled.system.controller.kp == 0.25
    assert compiled.system.controller.kd == 0.075


def test_scenario_compiler_applies_sensor_parameters(tmp_path):
    mapping = _scenario_mapping(tmp_path / "sensors")
    mapping["sensors"] = {
        "attitude": {
            "noise_std_rad": 0.0012,
        },
        "gyro": {
            "noise_std_rad_s": 0.004,
            "bias_std_rad_s": 0.005,
            "bias_rw_scale": 0.03,
        },
    }
    compiled = compile_scenario(scenario_from_mapping(mapping))

    assert compiled.system.sensors.attitude.noise_std_rad == 0.0012
    assert compiled.system.sensors.gyro.noise_std_rad_s == 0.004
    assert compiled.system.sensors.gyro.bias_std_rad_s == 0.005
    assert compiled.system.sensors.gyro.bias_rw_scale == 0.03


def test_json_scenario_loads_and_study_runner_writes_outputs(tmp_path):
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(
        json.dumps(_scenario_mapping(tmp_path / "study"), ensure_ascii=False),
        encoding="utf-8",
    )

    summary = StudyRunner(load_scenario(scenario_path)).run()
    row = summary.metrics_table()[0]
    output = tmp_path / "study"

    assert row["scenario"] == "platform_smoke"
    assert (output / "manifest.json").exists()
    assert (output / "metrics.csv").exists()
    assert (output / "time_history.csv").exists()
    assert (output / "events.csv").exists()
    assert (output / "README.md").exists()
    assert (output / "summary_metrics.csv").exists()
    assert (output / "study_manifest.json").exists()
    assert (output / "index.json").exists()
    assert (output / "experiment_manifest.json").exists()
    assert (output / "dashboard.html").exists()

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["scenario"]["metadata"]["name"] == "platform_smoke"
    assert manifest["samples"] == 8
    index = json.loads((output / "index.json").read_text(encoding="utf-8"))
    experiment_manifest = json.loads((output / "experiment_manifest.json").read_text(encoding="utf-8"))
    assert index["run_count"] == 1
    assert index["accepted_count"] == 1
    assert index["failed_count"] == 0
    assert index["best_run_id"] == "run_000"
    assert "final_error_deg" in index["metric_columns"]
    assert experiment_manifest["experiment"]["scenario"]["metadata"]["name"] == "platform_smoke"

    with (output / "metrics.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["run_id"] == "run_000"
    with (output / "summary_metrics.csv").open(newline="", encoding="utf-8") as handle:
        summary_rows = list(csv.DictReader(handle))
    assert summary_rows[0]["output_dir"] == str(output)
    assert summary_rows[0]["seed"] == "5"
    assert summary_rows[0]["fault_count"] == "0"
    assert summary_rows[0]["accepted"] == "True"
    with (output / "events.csv").open(newline="", encoding="utf-8") as handle:
        event_rows = list(csv.DictReader(handle))
    assert event_rows == []


def test_acceptance_criteria_are_recorded(tmp_path):
    mapping = _scenario_mapping(tmp_path / "acceptance")
    mapping["acceptance"] = {
        "max_final_error_deg": 0.0,
    }
    summary = StudyRunner(mapping).run()
    manifest = json.loads((tmp_path / "acceptance" / "manifest.json").read_text(encoding="utf-8"))
    with (tmp_path / "acceptance" / "metrics.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert summary.metrics_table()[0]["accepted"] is False
    assert "final_error_deg" in summary.metrics_table()[0]["failed_acceptance"]
    assert summary.acceptance_summary()["failed_count"] == 1
    assert manifest["acceptance"]["accepted"] is False
    assert rows[0]["accepted"] == "False"


def test_orbital_environment_scenario_runs_from_json(tmp_path):
    mapping = _scenario_mapping(tmp_path / "orbital")
    mapping["system"]["environment"] = "orbital"
    mapping["environment"] = {
        "epoch_utc": "2026-01-01T00:00:00Z",
        "sun_vector_eci": [1.0, 0.2, 0.1],
        "orbit": {
            "provider": "keplerian",
            "semi_major_axis_m": 6878137.0,
            "eccentricity": 0.001,
            "inclination_deg": 97.6,
            "raan_deg": 15.0,
            "arg_periapsis_deg": 0.0,
            "mean_anomaly0_deg": 10.0,
        },
    }
    scenario_path = tmp_path / "orbital.json"
    scenario_path.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")

    summary = StudyRunner(scenario_path).run()
    manifest = json.loads((tmp_path / "orbital" / "manifest.json").read_text(encoding="utf-8"))

    assert summary.metrics_table()[0]["final_error_deg"] >= 0.0
    assert manifest["scenario"]["system"]["environment"] == "orbital"
    assert manifest["scenario"]["environment"]["orbit"]["provider"] == "keplerian"


def test_cubesat_wheel_config_and_initial_fault_compile(tmp_path):
    mapping = _scenario_mapping(tmp_path / "wheels")
    mapping["system"]["builder"] = "cubesat_reaction_wheel"
    mapping["controller"] = {"pd_kp": 0.05, "pd_kd": 0.02}
    mapping["actuators"] = {
        "reaction_wheels": {
            "layout": "pyramid_4wheel",
            "max_torque_nm": 0.003,
            "initial_speeds_rad_s": [10.0, -20.0, 30.0, -40.0],
            "allocation": "nullspace_momentum",
            "momentum_gain": 0.1,
        }
    }
    mapping["faults"] = [
        {
            "target": "reaction_wheel",
            "action": "disable",
            "index": 2,
            "when_s": 0.0,
        }
    ]
    spec = scenario_from_mapping(mapping)
    compiled = compile_scenario(spec)
    result = ScenarioRunner(compiled.system).run(compiled.config)

    assert compiled.system.actuator.config.allocation == "nullspace_momentum"
    assert compiled.system.actuator.wheels[0].config.max_torque_nm == 0.003
    assert compiled.system.actuator.wheels[2].enabled is False
    assert result.wheel_speeds_rad_s.shape[1] == 4

    summary = StudyRunner(spec).run()
    events_path = tmp_path / "wheels" / "events.csv"
    with events_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert summary.metrics_table()[0]["fault_count"] == 1
    assert rows[0]["event_type"] == "fault"
    assert rows[0]["target"] == "reaction_wheel"
    assert rows[0]["action"] == "disable"
    assert rows[0]["index"] == "2"


def test_run_scenario_cli_writes_outputs(tmp_path, capsys):
    scenario_path = tmp_path / "scenario.json"
    output = tmp_path / "cli-output"
    scenario_path.write_text(
        json.dumps(_scenario_mapping(tmp_path / "ignored"), ensure_ascii=False),
        encoding="utf-8",
    )

    run_scenario_main([str(scenario_path), "--output", str(output)])
    captured = capsys.readouterr()

    assert "Output:" in captured.out
    assert "Final error deg:" in captured.out
    assert (output / "manifest.json").exists()
    assert (output / "metrics.csv").exists()


def test_run_scenario_cli_supports_overrides_and_sweeps(tmp_path, capsys):
    scenario_path = tmp_path / "scenario.json"
    output = tmp_path / "cli-sweep"
    scenario_path.write_text(
        json.dumps(_scenario_mapping(tmp_path / "ignored"), ensure_ascii=False),
        encoding="utf-8",
    )

    run_scenario_main(
        [
            str(scenario_path),
            "--output",
            str(output),
            "--set",
            "time.seed=9",
            "--sweep",
            "controller.pd_kp=0.2,0.3",
        ]
    )
    captured = capsys.readouterr()

    assert "Runs: 2" in captured.out
    assert "Accepted: 2" in captured.out
    assert "Failed: 0" in captured.out
    assert "Best run:" in captured.out
    assert (output / "README.md").exists()
    assert (output / "run_000" / "manifest.json").exists()
    assert (output / "run_001" / "manifest.json").exists()
    assert (output / "summary_metrics.csv").exists()
    assert (output / "study_manifest.json").exists()
    assert (output / "index.json").exists()
    assert (output / "experiment_manifest.json").exists()
    first_manifest = json.loads((output / "run_000" / "manifest.json").read_text(encoding="utf-8"))
    second_manifest = json.loads((output / "run_001" / "manifest.json").read_text(encoding="utf-8"))
    index = json.loads((output / "index.json").read_text(encoding="utf-8"))
    assert first_manifest["scenario"]["time"]["seed"] == 9
    assert first_manifest["scenario"]["controller"]["pd_kp"] == 0.2
    assert second_manifest["scenario"]["controller"]["pd_kp"] == 0.3
    assert index["run_count"] == 2
    assert index["accepted_count"] == 2
    assert index["failed_count"] == 0
    assert index["best_run_id"] in {"run_000", "run_001"}
    assert index["parameter_columns"] == ["param_controller.pd_kp"]


def test_experiment_plan_rejects_unknown_fields(tmp_path):
    mapping = {
        "schema_version": 1,
        "scenario": _scenario_mapping(tmp_path / "unused"),
        "unexpected": True,
    }

    with pytest.raises(ValueError, match="unknown experiment"):
        experiment_plan_from_mapping(mapping)


def test_experiment_plan_rejects_unknown_runtime_fields(tmp_path):
    mapping = {
        "schema_version": 1,
        "scenario": _scenario_mapping(tmp_path / "unused"),
        "runtime": {
            "name": "flight",
            "tasks": [],
            "unexpected": True,
        },
    }

    with pytest.raises(ValueError, match="unknown runtime process"):
        experiment_plan_from_mapping(mapping)


def test_experiment_runner_writes_platform_manifest_for_sweep(tmp_path):
    output = tmp_path / "experiment"
    plan = {
        "schema_version": 1,
        "metadata": {"name": "platform_experiment"},
        "scenario": _scenario_mapping(tmp_path / "ignored"),
        "sweeps": [{"path": "controller.pd_kp", "values": [0.2, 0.3]}],
        "outputs": {"root": str(output)},
    }

    summary = ExperimentRunner(plan).run()
    index = json.loads((output / "index.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "experiment_manifest.json").read_text(encoding="utf-8"))

    assert len(summary.metrics_table()) == 2
    assert (output / "run_000" / "manifest.json").exists()
    assert (output / "run_001" / "manifest.json").exists()
    assert index["run_count"] == 2
    assert index["parameter_columns"] == ["param_controller.pd_kp"]
    assert manifest["experiment"]["metadata"]["name"] == "platform_experiment"
    assert manifest["experiment"]["sweeps"][0]["path"] == "controller.pd_kp"


def test_experiment_runner_writes_runtime_and_mission_outputs(tmp_path):
    output = tmp_path / "runtime-mission"
    plan = {
        "schema_version": 1,
        "metadata": {"name": "runtime_mission_experiment"},
        "scenario": _scenario_mapping(tmp_path / "ignored"),
        "runtime": {
            "name": "flight",
            "tasks": [
                {
                    "name": "control",
                    "update_period_s": 0.2,
                    "modules": [
                        {"name": "gyro", "role": "sensor", "update_period_s": 0.1},
                        {"name": "pd_controller", "role": "controller"},
                    ],
                }
            ],
        },
        "mission": {
            "steps": [
                {"name": "detumble", "start_s": 0.0, "stop_s": 0.2, "mode": "detumble"},
                {"name": "hold", "start_s": 0.2, "stop_s": 0.4, "mode": "inertial_hold", "reference": "body_zero"},
            ]
        },
        "outputs": {"root": str(output)},
    }

    summary = ExperimentRunner(plan).run()
    index = json.loads((output / "index.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "experiment_manifest.json").read_text(encoding="utf-8"))
    schedule = json.loads((output / "runtime_schedule.json").read_text(encoding="utf-8"))
    timeline = json.loads((output / "mode_timeline.json").read_text(encoding="utf-8"))
    readme = (output / "README.md").read_text(encoding="utf-8")
    dashboard = (output / "dashboard.html").read_text(encoding="utf-8")

    assert "runtime_schedule" in summary.write_outputs()
    assert "mode_timeline" in summary.write_outputs()
    assert "dashboard" in summary.write_outputs()
    assert index["runtime_schedule"] == "runtime_schedule.json"
    assert index["mode_timeline"] == "mode_timeline.json"
    assert index["dashboard"] == "dashboard.html"
    assert manifest["experiment"]["runtime"]["name"] == "flight"
    assert manifest["experiment"]["mission"]["steps"][1]["mode"] == "inertial_hold"
    assert schedule["event_count"] == len(schedule["events"])
    assert schedule["events"][0]["module"] == "gyro"
    assert timeline["timeline"][1]["reference"] == "body_zero"
    assert "`runtime_schedule.json`" in readme
    assert "`mode_timeline.json`" in readme
    assert "Run Metrics" in dashboard
    assert "Mode Timeline" in dashboard
    assert "runtime_mission_experiment" in dashboard
    assert '"dashboard_output_href": "README.md"' in dashboard


def test_experiment_plan_runtime_and_mission_templates_use_scenario_timing(tmp_path):
    output = tmp_path / "template-runtime-mission"
    plan = {
        "schema_version": 1,
        "metadata": {"name": "template_runtime_mission"},
        "scenario": _scenario_mapping(tmp_path / "ignored"),
        "runtime": {"template": "single_rate"},
        "mission": {
            "template": "detumble_then_hold",
            "detumble_s": 0.2,
            "hold_mode": "earth_pointing",
            "reference": "nadir",
        },
        "outputs": {"root": str(output)},
    }

    ExperimentRunner(plan).run()
    manifest = json.loads((output / "experiment_manifest.json").read_text(encoding="utf-8"))
    schedule = json.loads((output / "runtime_schedule.json").read_text(encoding="utf-8"))
    timeline = json.loads((output / "mode_timeline.json").read_text(encoding="utf-8"))

    assert manifest["experiment"]["runtime"]["metadata"]["template"] == "single_rate"
    assert manifest["experiment"]["runtime"]["metadata"]["dt_s"] == 0.05
    assert schedule["duration_s"] == 0.4
    assert schedule["events"][0]["module"] == "environment"
    assert timeline["duration_s"] == 0.4
    assert timeline["timeline"][1]["mode"] == "earth_pointing"
    assert timeline["timeline"][1]["reference"] == "nadir"


def test_experiment_plan_loads_relative_scenario_path(tmp_path):
    scenario_path = tmp_path / "scenario.json"
    plan_path = tmp_path / "plan.json"
    output = tmp_path / "relative-output"
    scenario_path.write_text(json.dumps(_scenario_mapping(tmp_path / "ignored"), ensure_ascii=False), encoding="utf-8")
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "metadata": {"name": "relative_plan"},
                "scenario": "scenario.json",
                "monte_carlo": {"samples": 2, "seed": 30},
                "outputs": {"root": str(output)},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = ExperimentRunner(plan_path).run()
    rows = summary.metrics_table()

    assert [row["seed"] for row in rows] == [30, 31]
    assert (output / "experiment_manifest.json").exists()


def test_platform_project_runs_plan_in_workspace(tmp_path):
    project = PlatformProject(tmp_path / "workspace")
    plan = {
        "schema_version": 1,
        "metadata": {"name": "workspace_plan"},
        "scenario": _scenario_mapping(tmp_path / "ignored"),
    }

    summary = project.run(plan)

    assert summary.output_dir == tmp_path / "workspace" / "results" / "workspace_plan"
    assert (summary.output_dir / "experiment_manifest.json").exists()


def test_study_runner_monte_carlo_creates_seeded_runs(tmp_path):
    spec = scenario_from_mapping(_scenario_mapping(tmp_path / "mc"))
    summary = StudyRunner(spec).run(MonteCarlo(samples=3, seed=20))
    rows = summary.metrics_table()

    assert len(rows) == 3
    assert [row["seed"] for row in rows] == [20, 21, 22]
    assert rows[0]["param_time.seed"] == 20
    assert rows[0]["param_monte_carlo.sample"] == 0
    assert rows[2]["param_monte_carlo.sample"] == 2
    assert (tmp_path / "mc" / "run_000" / "manifest.json").exists()
    assert (tmp_path / "mc" / "run_001" / "manifest.json").exists()
    assert (tmp_path / "mc" / "run_002" / "manifest.json").exists()


def test_run_scenario_cli_supports_monte_carlo(tmp_path, capsys):
    scenario_path = tmp_path / "scenario.json"
    output = tmp_path / "cli-mc"
    scenario_path.write_text(
        json.dumps(_scenario_mapping(tmp_path / "ignored"), ensure_ascii=False),
        encoding="utf-8",
    )

    run_scenario_main(
        [
            str(scenario_path),
            "--output",
            str(output),
            "--monte-carlo",
            "2",
            "--monte-carlo-seed",
            "50",
        ]
    )
    captured = capsys.readouterr()

    assert "Runs: 2" in captured.out
    assert "Accepted: 2" in captured.out
    assert "Failed: 0" in captured.out
    assert (output / "run_000" / "manifest.json").exists()
    assert (output / "run_001" / "manifest.json").exists()
    with (output / "summary_metrics.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["seed"] == "50"
    assert rows[1]["seed"] == "51"

    readme = (output / "README.md").read_text(encoding="utf-8")
    assert "Best run:" in readme
    assert "Accepted:" in readme
    assert "`param_time.seed`" in readme
    assert "`param_monte_carlo.sample`" in readme


def test_run_scenario_cli_reports_failed_acceptance_summary(tmp_path, capsys):
    scenario_path = tmp_path / "scenario.json"
    output = tmp_path / "cli-failed"
    mapping = _scenario_mapping(tmp_path / "ignored")
    mapping["acceptance"] = {"max_final_error_deg": 0.0}
    scenario_path.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")

    run_scenario_main(
        [
            str(scenario_path),
            "--output",
            str(output),
            "--sweep",
            "time.seed=1,2",
        ]
    )
    captured = capsys.readouterr()
    index = json.loads((output / "index.json").read_text(encoding="utf-8"))

    assert "Runs: 2" in captured.out
    assert "Accepted: 0" in captured.out
    assert "Failed: 2" in captured.out
    assert index["accepted_count"] == 0
    assert index["failed_count"] == 2


def test_run_experiment_cli_writes_outputs(tmp_path, capsys):
    plan_path = tmp_path / "plan.json"
    output = tmp_path / "cli-experiment"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "metadata": {"name": "cli_experiment"},
                "scenario": _scenario_mapping(tmp_path / "ignored"),
                "sweeps": [{"path": "controller.pd_kp", "values": [0.2, 0.3]}],
                "outputs": {"root": str(tmp_path / "ignored-output")},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    run_experiment_main([str(plan_path), "--output", str(output)])
    captured = capsys.readouterr()

    assert "Output:" in captured.out
    assert "Runs: 2" in captured.out
    assert "Dashboard:" in captured.out
    assert (output / "experiment_manifest.json").exists()
    assert (output / "run_000" / "manifest.json").exists()
    assert (output / "dashboard.html").exists()
    assert "run_000/README.md" in (output / "dashboard.html").read_text(encoding="utf-8")


def test_build_dashboard_cli_writes_static_interface(tmp_path, capsys):
    output = tmp_path / "dashboard-output"
    plan = {
        "schema_version": 1,
        "metadata": {"name": "dashboard_experiment"},
        "scenario": _scenario_mapping(tmp_path / "ignored"),
        "runtime": {"template": "single_rate"},
        "mission": {"template": "single_mode", "mode": "safe"},
        "outputs": {"root": str(output)},
    }
    ExperimentRunner(plan).run()
    (output / "dashboard.html").unlink()

    build_dashboard_main([str(output)])
    captured = capsys.readouterr()
    html = (output / "dashboard.html").read_text(encoding="utf-8")

    assert "Dashboard:" in captured.out
    assert "dashboard_experiment" in html
    assert "Runtime Schedule" in html
    assert "Mode Timeline" in html


def test_platform_webapp_discovers_validates_and_runs_experiment(tmp_path):
    workspace = tmp_path / "workspace"
    scenario_dir = workspace / "scenarios"
    scenario_dir.mkdir(parents=True)
    scenario_path = scenario_dir / "scenario.json"
    plan_path = scenario_dir / "plan.json"
    scenario_path.write_text(json.dumps(_scenario_mapping(workspace / "ignored"), ensure_ascii=False), encoding="utf-8")
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "metadata": {"name": "webapp_plan"},
                "scenario": "scenario.json",
                "runtime": {"template": "single_rate"},
                "mission": {"template": "single_mode", "mode": "safe"},
                "outputs": {"root": str(workspace / "ignored-output")},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    discovered = discover_workspace(workspace)
    validation = validate_workspace_experiment(workspace, "scenarios/plan.json")
    result = run_workspace_experiment(workspace, "scenarios/plan.json", "results/webapp_plan")
    rediscovered = discover_workspace(workspace)

    assert discovered["experiments"][0]["name"] == "webapp_plan"
    assert validation["valid"] is True
    assert validation["runs"] == 1
    assert result["runs"] == 1
    assert result["dashboard_url"] == "/file/results/webapp_plan/dashboard.html"
    assert (workspace / "results" / "webapp_plan" / "dashboard.html").exists()
    assert rediscovered["dashboards"][0]["url"] == "/file/results/webapp_plan/dashboard.html"


def test_platform_webapp_creates_experiment_plan_from_scenario(tmp_path):
    workspace = tmp_path / "workspace"
    scenario_dir = workspace / "scenarios"
    scenario_dir.mkdir(parents=True)
    scenario_path = scenario_dir / "scenario.json"
    scenario_path.write_text(json.dumps(_scenario_mapping(workspace / "ignored"), ensure_ascii=False), encoding="utf-8")

    created = create_workspace_experiment_plan(
        workspace,
        {
            "scenario_path": "scenarios/scenario.json",
            "name": "Generated PD Sweep",
            "sweep_path": "controller.pd_kp",
            "sweep_values": "0.2,0.3",
            "monte_carlo_samples": 2,
            "monte_carlo_seed": 12,
            "mission_template": "detumble_then_hold",
            "detumble_s": 0.2,
            "hold_mode": "sun_pointing",
            "reference": "sun",
        },
    )
    plan_path = workspace / created["path"]
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    validation = validate_workspace_experiment(workspace, created["path"])

    assert created["path"] == "scenarios/Generated_PD_Sweep.json"
    assert created["validation"]["runs"] == 4
    assert validation["runs"] == 4
    assert plan["scenario"] == "scenario.json"
    assert plan["sweeps"][0]["values"] == [0.2, 0.3]
    assert plan["monte_carlo"]["seed"] == 12
    assert plan["runtime"]["template"] == "single_rate"
    assert plan["mission"]["hold_mode"] == "sun_pointing"


def test_validate_experiment_cli_does_not_write_outputs(tmp_path, capsys):
    plan_path = tmp_path / "plan.json"
    output = tmp_path / "validate-experiment-output"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "metadata": {"name": "validate_experiment"},
                "scenario": _scenario_mapping(tmp_path / "ignored"),
                "monte_carlo": {"samples": 2, "seed": 40},
                "outputs": {"root": str(output)},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    validate_experiment_main([str(plan_path)])
    captured = capsys.readouterr()

    assert "Valid experiment:" in captured.out
    assert "Runs: 2" in captured.out
    assert not output.exists()


def test_validate_scenario_cli_does_not_write_outputs(tmp_path, capsys):
    scenario_path = tmp_path / "scenario.json"
    output = tmp_path / "validate-output"
    scenario_path.write_text(
        json.dumps(_scenario_mapping(output), ensure_ascii=False),
        encoding="utf-8",
    )

    validate_scenario_main([str(scenario_path), "--sweep", "controller.pd_kp=0.2,0.3"])
    captured = capsys.readouterr()

    assert "Valid scenario:" in captured.out
    assert "Runs: 2" in captured.out
    assert not output.exists()


def test_validate_scenario_cli_supports_monte_carlo_without_outputs(tmp_path, capsys):
    scenario_path = tmp_path / "scenario.json"
    output = tmp_path / "validate-mc-output"
    scenario_path.write_text(
        json.dumps(_scenario_mapping(output), ensure_ascii=False),
        encoding="utf-8",
    )

    validate_scenario_main([str(scenario_path), "--monte-carlo", "2", "--monte-carlo-seed", "50"])
    captured = capsys.readouterr()

    assert "Runs: 2" in captured.out
    assert not output.exists()


def test_repo_scenario_templates_validate(tmp_path, capsys):
    validate_scenario_main(["scenarios/quick_pd_zero.json"])
    validate_scenario_main(["scenarios/cubesat_rw_fault.json", "--set", f"outputs.root={json.dumps(str(tmp_path / 'unused'))}"])
    validate_experiment_main(["scenarios/quick_pd_experiment.json"])
    captured = capsys.readouterr()

    assert "quick_pd_zero" in captured.out
    assert "cubesat_rw_fault" in captured.out
    assert "quick_pd_experiment" in captured.out


def test_study_runner_sweep_creates_run_directories(tmp_path):
    spec = scenario_from_mapping(_scenario_mapping(tmp_path / "sweep"))
    summary = StudyRunner(spec).run(Sweep("time.seed", [1, 2]))

    assert len(summary.metrics_table()) == 2
    assert (tmp_path / "sweep" / "README.md").exists()
    assert (tmp_path / "sweep" / "summary_metrics.csv").exists()
    assert (tmp_path / "sweep" / "study_manifest.json").exists()
    assert (tmp_path / "sweep" / "index.json").exists()
    assert (tmp_path / "sweep" / "run_000" / "manifest.json").exists()
    assert (tmp_path / "sweep" / "run_001" / "manifest.json").exists()
    assert summary.metrics_table()[0]["param_time.seed"] == 1
    assert summary.metrics_table()[1]["param_time.seed"] == 2
    assert summary.acceptance_summary()["run_count"] == 2
    assert summary.best_row()["run_id"] in {"run_000", "run_001"}
    assert summary.parameter_columns() == ["param_time.seed"]
    assert "final_error_deg" in summary.metric_columns()
