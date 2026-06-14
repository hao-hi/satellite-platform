"""Lightweight v0.2 platform workflow tests."""

import csv
import json
from pathlib import Path

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
    _render_home,
    archive_workspace_experiment,
    create_workspace_experiment_plan,
    duplicate_workspace_experiment,
    describe_workspace_experiment,
    describe_workspace_dashboard,
    describe_workspace_scenario,
    discover_workspace,
    platform_ui_health,
    rename_workspace_experiment,
    restore_workspace_experiment,
    run_workspace_experiment,
    save_workspace_experiment,
    validate_workspace_scenario,
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


def test_scenario_compiler_applies_disturbance_profile(tmp_path):
    mapping = _scenario_mapping(tmp_path / "disturbance-profile")
    mapping["system"]["environment"] = "orbital"
    mapping["system"]["disturbance_profile"] = "solar_pressure_only"
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
    summary = StudyRunner(mapping).run()
    row = summary.metrics_table()[0]

    assert row["peak_solar_pressure_torque_nm"] >= 0.0
    assert "peak_gravity_gradient_torque_nm" not in row
    assert "peak_residual_magnetic_torque_nm" not in row


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
    assert "peak_disturbance_torque_nm" in index["metric_columns"]
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
    with (output / "time_history.csv").open(newline="", encoding="utf-8") as handle:
        time_history_rows = list(csv.DictReader(handle))
    assert "true_qw" in time_history_rows[0]
    assert "estimated_qw" in time_history_rows[0]
    assert "reference_qw" in time_history_rows[0]
    assert "gravity_gradient_torque_norm_nm" in time_history_rows[0]
    assert "solar_pressure_torque_norm_nm" in time_history_rows[0]
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
    assert "peak_gravity_gradient_torque_nm" in summary.metrics_table()[0]
    assert "mean_solar_pressure_torque_nm" in summary.metrics_table()[0]
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
    assert "指标总览" in dashboard
    assert "仿真结果图" in dashboard
    assert "姿态误差动画" in dashboard
    assert "诊断摘要" in dashboard
    assert "验收失败原因" in dashboard
    assert "动态峰值摘要" in dashboard
    assert "扰动力矩预算" in dashboard
    assert "环境扰动分解" in dashboard
    assert "主导扰动项" in dashboard
    assert "最差 run 解释" in dashboard
    assert "任务模式时间线" in dashboard
    assert "runtime_mission_experiment" in dashboard
    assert '"dashboard_output_href": "README.md"' in dashboard
    assert '"time_history"' in dashboard


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
    assert "运行时调度" in html
    assert "任务模式时间线" in html
    assert "仿真结果图" in html


def test_dashboard_loads_time_history_for_relative_output_dirs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    output = "results/relative-dashboard"
    plan = {
        "schema_version": 1,
        "metadata": {"name": "relative_dashboard"},
        "scenario": _scenario_mapping("ignored"),
        "outputs": {"root": output},
    }

    ExperimentRunner(plan).run()
    html = (tmp_path / output / "dashboard.html").read_text(encoding="utf-8")

    assert '"time_history": {"run_000"' in html
    assert "attitude_error_deg" in html
    assert "姿态误差动画" in html


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
    scenario_details = describe_workspace_scenario(workspace, "scenarios/scenario.json")
    scenario_validation = validate_workspace_scenario(workspace, "scenarios/scenario.json")
    validation = validate_workspace_experiment(workspace, "scenarios/plan.json")
    result = run_workspace_experiment(workspace, "scenarios/plan.json", "results/webapp_plan")
    dashboard_details = describe_workspace_dashboard(workspace, "results/webapp_plan/dashboard.html")
    rediscovered = discover_workspace(workspace)

    assert discovered["experiments"][0]["name"] == "webapp_plan"
    assert "updated_at" in discovered["experiments"][0]
    assert discovered["scenarios"][0]["name"] == "platform_smoke"
    assert scenario_details["controller"] == "pd"
    assert scenario_details["duration_s"] == 0.4
    assert scenario_validation["valid"] is True
    assert scenario_validation["system"] == "default"
    assert validation["valid"] is True
    assert validation["runs"] == 1
    assert result["runs"] == 1
    assert result["summary"]["experiment_name"] == "webapp_plan"
    assert result["summary"]["run_count"] == 1
    assert result["summary"]["compare_run_ids"] == ["run_000"]
    assert "run_000" in result["summary"]["compare_histories"]
    assert "true_qw" in result["summary"]["compare_histories"]["run_000"][0]
    assert result["summary"]["timeline"]["timeline"][0]["mode"] == "safe"
    assert result["summary"]["runtime"]["snapshots"][0]["task"] == "attitude_step"
    assert result["summary"]["runtime"]["snapshots"][0]["modules"][0] == "environment"
    assert result["dashboard_url"] == "/file/results/webapp_plan/dashboard.html"
    assert dashboard_details["scenario_name"] == "platform_smoke"
    assert dashboard_details["best_run_id"] == "run_000"
    assert dashboard_details["run_count"] == 1
    assert dashboard_details["compare_run_ids"] == ["run_000"]
    assert dashboard_details["compare_histories"]["run_000"][0]["attitude_error_deg"] >= 0.0
    assert dashboard_details["compare_histories"]["run_000"][0]["true_qw"] >= 0.0
    assert dashboard_details["runs"][0]["history_summary"]["samples"] >= 1
    assert dashboard_details["runs"][0]["history_summary"]["peak_omega_rad_s"] >= 0.0
    assert dashboard_details["runs"][0]["history_summary"]["peak_disturbance_torque_nm"] >= 0.0
    assert "dominant_disturbance_term" in dashboard_details["runs"][0]["history_summary"]
    assert "README.md" in dashboard_details["runs"][0]["artifacts"]
    assert "time_history.csv" in dashboard_details["runs"][0]["artifacts"]
    assert dashboard_details["timeline"]["timeline"][0]["mode"] == "safe"
    assert dashboard_details["runtime"]["snapshots"][0]["task"] == "attitude_step"
    assert dashboard_details["files"][-1]["name"] == "dashboard.html"
    assert (workspace / "results" / "webapp_plan" / "dashboard.html").exists()
    assert rediscovered["dashboards"][0]["run_count"] == 1
    assert rediscovered["dashboards"][0]["acceptance_rate"] == 1.0
    assert rediscovered["dashboards"][0]["url"] == "/file/results/webapp_plan/dashboard.html"
    assert "updated_at" in rediscovered["dashboards"][0]


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
    assert created["output_root"] == "results/platform_ui/Generated_PD_Sweep"
    assert created["validation"]["runs"] == 4
    assert validation["runs"] == 4
    assert plan["scenario"] == "scenario.json"
    assert plan["sweeps"][0]["values"] == [0.2, 0.3]
    assert plan["monte_carlo"]["seed"] == 12
    assert plan["runtime"]["template"] == "single_rate"
    assert plan["mission"]["hold_mode"] == "sun_pointing"


def test_platform_webapp_creates_two_dimensional_sweep_experiment_plan(tmp_path):
    workspace = tmp_path / "workspace"
    scenario_dir = workspace / "scenarios"
    scenario_dir.mkdir(parents=True)
    scenario_path = scenario_dir / "scenario.json"
    mapping = _scenario_mapping(workspace / "ignored")
    mapping["system"]["builder"] = "cubesat_reaction_wheel"
    mapping["system"]["environment"] = "orbital"
    mapping["actuators"] = {
        "reaction_wheels": {
            "layout": "pyramid_4wheel",
            "spin_inertia_kgm2": 0.000026,
            "max_torque_nm": 0.007,
            "max_speed_rad_s": 837.758041,
            "initial_speeds_rad_s": [10.0, -20.0, 30.0, -40.0],
            "allocation": "bounded_pinv",
            "momentum_reference_rad_s": 0.0,
            "momentum_gain": 0.0,
            "wheel_torque_weights": 1.0,
        }
    }
    scenario_path.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")

    created = create_workspace_experiment_plan(
        workspace,
        {
            "scenario_path": "scenarios/scenario.json",
            "name": "disturbance_capability_tradeoff",
            "sweep_path": "system.disturbance_profile",
            "sweep_values": '"aerodynamic_only","all"',
            "second_sweep_path": "actuators.reaction_wheels.max_torque_nm",
            "second_sweep_values": "0.003,0.005,0.007",
            "mission_template": "single_mode",
            "mode": "inertial_hold",
            "reference": "body_zero",
        },
    )
    plan = json.loads((workspace / created["path"]).read_text(encoding="utf-8"))

    assert created["validation"]["runs"] == 6
    assert len(plan["sweeps"]) == 2
    assert plan["sweeps"][0]["path"] == "system.disturbance_profile"
    assert plan["sweeps"][1]["path"] == "actuators.reaction_wheels.max_torque_nm"
    assert plan["sweeps"][1]["values"] == [0.003, 0.005, 0.007]


def test_platform_webapp_can_describe_and_save_experiment_plan(tmp_path):
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
                "metadata": {"name": "editable_plan"},
                "scenario": "scenario.json",
                "outputs": {"root": "results/editable_plan"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    described = describe_workspace_experiment(workspace, "scenarios/plan.json")
    edited_mapping = dict(described["mapping"])
    edited_mapping["metadata"] = {"name": "editable_plan_v2", "description": "edited in UI"}
    edited_mapping["monte_carlo"] = {"samples": 3, "seed": 21}
    saved = save_workspace_experiment(
        workspace,
        "scenarios/plan.json",
        json.dumps(edited_mapping, ensure_ascii=False),
    )
    validation = validate_workspace_experiment(workspace, "scenarios/plan.json")

    assert described["name"] == "editable_plan"
    assert described["runs"] == 1
    assert saved["name"] == "editable_plan_v2"
    assert saved["runs"] == 3
    assert validation["name"] == "editable_plan_v2"
    assert validation["runs"] == 3
    assert json.loads(plan_path.read_text(encoding="utf-8"))["monte_carlo"]["samples"] == 3


def test_platform_webapp_can_duplicate_experiment_plan(tmp_path):
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
                "metadata": {"name": "base_plan"},
                "scenario": "scenario.json",
                "sweeps": [{"path": "controller.pd_kp", "values": [0.2, 0.3]}],
                "outputs": {"root": "results/base_plan"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    duplicated = duplicate_workspace_experiment(workspace, "scenarios/plan.json", "base_plan_variant")
    duplicated_plan = json.loads((workspace / duplicated["path"]).read_text(encoding="utf-8"))

    assert duplicated["path"] == "scenarios/base_plan_variant.json"
    assert duplicated["name"] == "base_plan_variant"
    assert duplicated["source_path"] == "scenarios/plan.json"
    assert duplicated["validation"]["runs"] == 2
    assert duplicated["output_root"] == "results/platform_ui/base_plan_variant"
    assert duplicated_plan["metadata"]["name"] == "base_plan_variant"
    assert duplicated_plan["outputs"]["root"] == "results/platform_ui/base_plan_variant"
    assert duplicated_plan["sweeps"][0]["values"] == [0.2, 0.3]


def test_platform_webapp_can_rename_experiment_plan(tmp_path):
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
                "metadata": {"name": "base_plan"},
                "scenario": "scenario.json",
                "outputs": {"root": "results/platform_ui/plan"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    renamed = rename_workspace_experiment(workspace, "scenarios/plan.json", "renamed_plan")
    renamed_plan = json.loads((workspace / renamed["path"]).read_text(encoding="utf-8"))

    assert renamed["path"] == "scenarios/renamed_plan.json"
    assert renamed["previous_name"] == "base_plan"
    assert renamed["name"] == "renamed_plan"
    assert renamed["source_path"] == "scenarios/plan.json"
    assert renamed["output_root"] == "results/platform_ui/renamed_plan"
    assert renamed["validation"]["runs"] == 1
    assert renamed_plan["metadata"]["name"] == "renamed_plan"
    assert renamed_plan["outputs"]["root"] == "results/platform_ui/renamed_plan"
    assert not plan_path.exists()


def test_platform_webapp_can_archive_experiment_plan(tmp_path):
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
                "metadata": {"name": "archivable_plan"},
                "scenario": "scenario.json",
                "outputs": {"root": "results/platform_ui/archivable_plan"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    archived = archive_workspace_experiment(workspace, "scenarios/plan.json")
    discovered = discover_workspace(workspace)

    assert archived["name"] == "archivable_plan"
    assert archived["source_path"] == "scenarios/plan.json"
    assert archived["archived_path"] == "scenarios/archive/plan.json"
    assert not plan_path.exists()
    assert (workspace / "scenarios" / "archive" / "plan.json").exists()
    assert discovered["experiments"] == []
    assert discovered["archived_experiments"][0]["name"] == "archivable_plan"


def test_platform_webapp_can_restore_archived_experiment_plan(tmp_path):
    workspace = tmp_path / "workspace"
    scenario_dir = workspace / "scenarios"
    archive_dir = scenario_dir / "archive"
    archive_dir.mkdir(parents=True)
    scenario_path = scenario_dir / "scenario.json"
    archived_path = archive_dir / "plan.json"
    scenario_path.write_text(json.dumps(_scenario_mapping(workspace / "ignored"), ensure_ascii=False), encoding="utf-8")
    archived_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "metadata": {"name": "restorable_plan"},
                "scenario": "../scenario.json",
                "outputs": {"root": "results/platform_ui/plan"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    restored = restore_workspace_experiment(workspace, "scenarios/archive/plan.json")
    discovered = discover_workspace(workspace)

    assert restored["path"] == "scenarios/restorable_plan.json"
    assert restored["name"] == "restorable_plan"
    assert restored["source_path"] == "scenarios/archive/plan.json"
    assert restored["validation"]["runs"] == 1
    assert (workspace / "scenarios" / "restorable_plan.json").exists()
    assert not archived_path.exists()
    assert discovered["experiments"][0]["name"] == "restorable_plan"
    assert discovered["archived_experiments"] == []


def test_platform_webapp_create_experiment_plan_auto_renames_on_collision(tmp_path):
    workspace = tmp_path / "workspace"
    scenario_dir = workspace / "scenarios"
    scenario_dir.mkdir(parents=True)
    scenario_path = scenario_dir / "scenario.json"
    scenario_path.write_text(json.dumps(_scenario_mapping(workspace / "ignored"), ensure_ascii=False), encoding="utf-8")
    (scenario_dir / "duplicate_plan.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "metadata": {"name": "duplicate_plan"},
                "scenario": "scenario.json",
                "outputs": {"root": "results/platform_ui/duplicate_plan"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    created = create_workspace_experiment_plan(
        workspace,
        {
            "scenario_path": "scenarios/scenario.json",
            "name": "duplicate_plan",
            "output_root": "results/platform_ui/duplicate_plan",
            "mission_template": "single_mode",
            "mode": "safe",
            "reference": "body_zero",
        },
    )
    created_plan = json.loads((workspace / created["path"]).read_text(encoding="utf-8"))

    assert created["requested_name"] == "duplicate_plan"
    assert created["resolved_from_collision"] is True
    assert created["name"] == "duplicate_plan_2"
    assert created["path"] == "scenarios/duplicate_plan_2.json"
    assert created["output_root"] == "results/platform_ui/duplicate_plan_2"
    assert created_plan["metadata"]["name"] == "duplicate_plan_2"
    assert created_plan["outputs"]["root"] == "results/platform_ui/duplicate_plan_2"


def test_platform_webapp_create_experiment_plan_keeps_description_and_acceptance(tmp_path):
    workspace = tmp_path / "workspace"
    scenario_dir = workspace / "scenarios"
    scenario_dir.mkdir(parents=True)
    scenario_path = scenario_dir / "scenario.json"
    scenario_path.write_text(json.dumps(_scenario_mapping(workspace / "ignored"), ensure_ascii=False), encoding="utf-8")

    created = create_workspace_experiment_plan(
        workspace,
        {
            "scenario_path": "scenarios/scenario.json",
            "name": "described_plan",
            "description": "Compare gain tuning quality under a strict acceptance template.",
            "output_root": "results/platform_ui/described_plan",
            "mission_template": "single_mode",
            "mode": "inertial_hold",
            "reference": "body_zero",
            "acceptance_final_deg": 20,
            "acceptance_rms_deg": 25,
            "acceptance_peak_torque_nm": 0.12,
        },
    )
    created_plan = json.loads((workspace / created["path"]).read_text(encoding="utf-8"))

    assert created_plan["metadata"]["description"] == "Compare gain tuning quality under a strict acceptance template."
    assert created_plan["acceptance"]["max_final_error_deg"] == 20
    assert created_plan["acceptance"]["max_rms_error_deg"] == 25
    assert created_plan["acceptance"]["max_peak_torque_nm"] == 0.12


def test_platform_webapp_home_contains_builder_result_panel():
    html = _render_home()

    assert "平台总览" in html
    assert "总览" in html
    assert "实验设计" in html
    assert "运行结果" in html
    assert "satmodel 平台" in html
    assert "平台总览" in html
    assert "实验库" in html
    assert "创建实验" in html
    assert "计划管理" in html
    assert "结果总览" in html
    assert "结果对比" in html
    assert "姿态回放" in html
    assert "Dashboard 预览" in html
    assert "最近实验计划" in html
    assert "最近实验结果" in html
    assert "待关注事项" in html
    assert "资料与扩展入口" in html
    assert "核心资料" in html
    assert "实验库建议" in html
    assert "可插拔扩展位" in html
    assert 'id="builder-result"' in html
    assert 'id="experiment-filter"' in html
    assert 'id="experiment-status-filter"' in html
    assert "查看新计划" in html
    assert "按计划名或场景筛选实验" in html
    assert "已有结果" in html
    assert "暂无结果" in html
    assert "查看对应计划" in html
    assert "重新运行这个计划" in html
    assert "Run 明细" in html
    assert "当前用于对比 A" in html
    assert "当前用于姿态回放" in html
    assert "与最佳 Run 的参数差异" in html
    assert "当前对比组合" in html
    assert "当前回放对象" in html
    assert "Run 排行与工作台" in html
    assert "查看某个 run 的明细" in html
    assert "送到对比 A" in html
    assert "送到对比 B" in html
    assert "设为回放 Run" in html
    assert "运行这个计划" in html
    assert "查看结果" in html
    assert "最近实验计划" in html
    assert "查看对应结果" in html
    assert "正在创建并运行实验" in html
    assert "创建实验时遇到问题" in html
    assert 'id="builder-section"' in html
    assert 'id="editor-section"' in html
    assert "运行状态" in html
    assert "最近一次运行摘要" in html
    assert 'id="run-progress-panel"' in html
    assert 'id="latest-run-summary-panel"' in html
    assert "当前阶段：" in html
    assert "关键指标概览" in html
    assert "按末端误差从优到劣展示前 5 个 run" in html
    assert "最近实验通过率趋势" in html
    assert "最佳误差" in html
    assert "快速配置" in html
    assert "高级配置" in html
    assert "实验设计工作台" in html
    assert "实验内容建议" in html
    assert "先选实验主线" in html
    assert "第二扫描变量模板" in html
    assert "当前建议的实验补强方向" in html
    assert "实验说明" in html
    assert "实验假设" in html
    assert "适用前提" in html
    assert "建议图表" in html
    assert "代表结果入口" in html
    assert "查看代表结果" in html
    assert "实验资产地图" in html
    assert "实验建设主线" in html
    assert "控制器整定" in html
    assert "随机鲁棒性" in html
    assert "任务模式切换" in html
    assert "执行器能力边界" in html
    assert "验收模板" in html
    assert "标准姿态保持" in html
    assert "严格闭环验证" in html
    assert "推荐实验库" in html
    assert "阻尼整定实验" in html
    assert "故障场景 Monte Carlo" in html
    assert "故障场景增益权衡" in html
    assert "控制器基准实验" in html
    assert "控制器基准演示" in html
    assert "环境敏感性实验" in html
    assert "轨道环境演示" in html
    assert "轨道环境控制器基准" in html
    assert "环境扰动分解实验" in html
    assert "扰动-执行器权衡实验" in html
    assert "扰动分解演示" in html
    assert "测量质量敏感性实验" in html
    assert "严格验收门限实验" in html
    assert "严格验收门限" in html
    assert "验收与评估" in html
    assert "轮速与动量管理实验" in html
    assert "测量质量演示" in html
    assert "实验分组" in html
    assert "当前分组实验" in html
    assert "环境模型" in html
    assert "扰动配置模板" in html
    assert "陀螺噪声强度" in html
    assert "轮组动量管理增益" in html
    assert "扰动-执行器权衡" in html
    assert "实验选择" in html
    assert "先在这里选要继续的实验" in html
    assert "实验池" in html
    assert "当前计划" in html
    assert "版本与归档" in html
    assert "场景与记录" in html
    assert "计划管理工作台" in html
    assert "实验主线" in html
    assert "资产状态" in html
    assert "快速选择实验计划" in html
    assert "当前计划画像" in html
    assert 'id="editor-quick-select"' in html
    assert "载入编辑" in html
    assert 'id="experiment-picker"' in html
    assert "1. 研究问题" in html
    assert "2. 场景与变量" in html
    assert "3. 任务与验收" in html
    assert "4. 预览与生成" in html
    assert "当前步骤：研究问题" in html
    assert "下一步：设计变量" in html
    assert "实验结论导读" in html
    assert "当前观察" in html
    assert "实验链路导航" in html
    assert "当前所处环节" in html
    assert "结果摘要标签" in html
    assert "概要" in html
    assert "诊断" in html
    assert "图表" in html
    assert "链路" in html
    assert "产物" in html
    assert "关键图表阅读面板" in html
    assert "scrollToCompareView()" in html
    assert "scrollToPreviewView()" in html
    assert "当前结论提示" in html
    assert "图表阅读提示" in html
    assert "最差 Run 解释" in html
    assert "打开现有计划" in html
    assert "载入同类模板" in html
    assert "继续到这组实验" in html
    assert 'id="create-plan" type="button"' in html
    assert "默认先显示常用配置" in html
    assert "常用配置" in html
    assert "结构概览" in html
    assert "JSON 编辑" in html
    assert "另存为副本" in html
    assert "重命名计划" in html
    assert "归档计划" in html
    assert "已归档计划" in html
    assert "恢复计划" in html
    assert "一键示例运行" in html
    assert "示例运行助手" in html
    assert "结果导览" in html
    assert "定位到 Run 排行" in html
    assert "定位到姿态回放" in html
    assert "快速闭环演示" in html
    assert "反作用轮故障演示" in html
    assert "太阳指向切换演示" in html
    assert "载入到创建器" in html
    assert 'id="archived-experiment-plans"' in html
    assert 'id="archived-experiment-count"' in html
    assert "复制计划" in html
    assert "扫描变量路径" in html
    assert "Monte Carlo 样本数" in html
    assert "输出目录" in html
    assert "输出与批量实验" in html
    assert "任务与运行时" in html
    assert "验收规则" in html
    assert "清空批量配置" in html
    assert "恢复默认流程" in html
    assert "预计 run 数" in html
    assert "扫描提示" in html
    assert "任务提示" in html


def test_curated_experiment_library_plans_validate():
    scenario_dir = Path(__file__).resolve().parents[1] / "scenarios"
    curated = [
        scenario_dir / "quick_pd_gain_sweep.json",
        scenario_dir / "quick_pd_damping_sweep.json",
        scenario_dir / "quick_pd_seed_mc.json",
        scenario_dir / "quick_controller_benchmark_compare.json",
        scenario_dir / "quick_environment_compare.json",
        scenario_dir / "cubesat_rw_disturbance_breakdown.json",
        scenario_dir / "quick_sensor_noise_sensitivity.json",
        scenario_dir / "cubesat_rw_fault_seed_mc.json",
        scenario_dir / "cubesat_rw_sun_transition_curated.json",
        scenario_dir / "cubesat_rw_fault_gain_tradeoff.json",
        scenario_dir / "cubesat_rw_wheel_capability.json",
        scenario_dir / "cubesat_rw_momentum_management_sweep.json",
    ]

    for plan_path in curated:
        runner = ExperimentRunner(plan_path)
        cases = runner.validate()
        assert len(cases) >= 1


def test_platform_ui_health_reports_workspace_summary(tmp_path):
    workspace = tmp_path / "workspace"
    scenario_dir = workspace / "scenarios"
    results_dir = workspace / "results"
    scenario_dir.mkdir(parents=True)
    results_dir.mkdir(parents=True)
    (scenario_dir / "scenario.json").write_text(
        json.dumps(_scenario_mapping(results_dir / "single"), ensure_ascii=False),
        encoding="utf-8",
    )

    health = platform_ui_health(workspace)

    assert health["status"] == "ok"
    assert health["workspace"] == str(workspace.resolve())
    assert health["scenario_count"] == 1
    assert health["experiment_count"] == 0
    assert health["archived_experiment_count"] == 0
    assert health["dashboard_count"] == 0


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
