"""Runtime and mission sequence skeleton tests."""

import pytest

from satmodel import (
    MissionSequence,
    RuntimeModule,
    RuntimeProcess,
    RuntimeTask,
    detumble_then_hold_mission,
    mission_sequence_from_mapping,
    runtime_process_from_mapping,
    single_mode_mission,
    single_rate_runtime_process,
)


def test_runtime_process_expands_multirate_schedule():
    process = RuntimeProcess(
        "flight",
        tasks=(
            RuntimeTask(
                "fast_io",
                update_period_s=0.1,
                priority=1,
                modules=(
                    RuntimeModule("gyro", role="sensor"),
                    RuntimeModule("recorder", role="recorder", update_period_s=0.2),
                ),
            ),
            RuntimeTask(
                "control",
                update_period_s=0.2,
                priority=2,
                modules=(RuntimeModule("pd_controller", role="controller"),),
            ),
        ),
    )

    schedule = process.schedule(duration_s=0.2)

    assert [(event["time_s"], event["task"], event["module"]) for event in schedule] == [
        (0.0, "control", "pd_controller"),
        (0.0, "fast_io", "gyro"),
        (0.0, "fast_io", "recorder"),
        (0.1, "fast_io", "gyro"),
        (0.2, "control", "pd_controller"),
        (0.2, "fast_io", "gyro"),
        (0.2, "fast_io", "recorder"),
    ]


def test_runtime_process_rejects_unknown_fields_and_duplicate_names():
    with pytest.raises(ValueError, match="unknown runtime process"):
        runtime_process_from_mapping({"name": "bad", "tasks": [], "surprise": True})

    with pytest.raises(ValueError, match="duplicate task names"):
        RuntimeProcess(
            "bad",
            tasks=(
                RuntimeTask("task", update_period_s=0.1),
                RuntimeTask("task", update_period_s=0.2),
            ),
        )


def test_runtime_task_time_window_and_disabled_modules():
    process = runtime_process_from_mapping(
        {
            "name": "windowed",
            "tasks": [
                {
                    "name": "control",
                    "update_period_s": 0.1,
                    "start_s": 0.1,
                    "stop_s": 0.3,
                    "modules": [
                        {"name": "controller", "role": "controller"},
                        {"name": "disabled", "enabled": False},
                    ],
                }
            ],
        }
    )

    assert [(event["time_s"], event["module"]) for event in process.schedule(0.5)] == [
        (0.1, "controller"),
        (0.2, "controller"),
        (0.3, "controller"),
    ]


def test_single_rate_runtime_template_preserves_scenario_step_order():
    process = single_rate_runtime_process(0.1, recorder_period_s=0.2)
    schedule = process.schedule(0.2)
    first_step = [event["module"] for event in schedule if event["time_s"] == 0.0]

    assert first_step == [
        "environment",
        "disturbance_model",
        "sensor_suite",
        "estimator",
        "controller",
        "actuator",
        "dynamics",
        "recorder",
    ]
    assert [event["time_s"] for event in schedule if event["module"] == "recorder"] == [0.0, 0.2]


def test_mission_sequence_mode_timeline_queries_modes():
    sequence = mission_sequence_from_mapping(
        {
            "metadata": {"name": "nominal_modes"},
            "steps": [
                {"name": "detumble", "start_s": 0.0, "stop_s": 10.0, "mode": "detumble"},
                {
                    "name": "hold",
                    "start_s": 10.0,
                    "stop_s": 20.0,
                    "mode": "inertial_hold",
                    "reference": "nadir",
                },
            ],
        }
    )

    timeline = sequence.mode_timeline()

    assert isinstance(sequence, MissionSequence)
    assert sequence.duration_s == 20.0
    assert timeline.mode_at(2.0) == "detumble"
    assert timeline.mode_at(12.0) == "inertial_hold"
    assert timeline.mode_at(25.0) is None
    assert sequence.active_step_at(12.0).reference == "nadir"


def test_mission_templates_cover_nominal_modes():
    safe = single_mode_mission("safe", 5.0)
    sequence = detumble_then_hold_mission(10.0, detumble_s=2.5, hold_mode="sun_pointing", reference="sun")

    assert safe.mode_timeline().mode_at(4.0) == "safe"
    assert sequence.mode_timeline().mode_at(1.0) == "detumble"
    assert sequence.mode_timeline().mode_at(3.0) == "sun_pointing"
    assert sequence.active_step_at(3.0).reference == "sun"


def test_mission_sequence_rejects_overlaps_and_unknown_fields():
    with pytest.raises(ValueError, match="unknown mission step"):
        mission_sequence_from_mapping(
            {
                "steps": [
                    {"name": "bad", "start_s": 0.0, "stop_s": 1.0, "mode": "hold", "extra": True},
                ]
            }
        )

    with pytest.raises(ValueError, match="overlaps"):
        mission_sequence_from_mapping(
            {
                "steps": [
                    {"name": "a", "start_s": 0.0, "stop_s": 2.0, "mode": "detumble"},
                    {"name": "b", "start_s": 1.0, "stop_s": 3.0, "mode": "inertial_hold"},
                ]
            }
        )


def test_mission_sequence_rejects_unknown_modes():
    with pytest.raises(ValueError, match="unsupported mission mode"):
        single_mode_mission("invented_mode", 1.0)

    with pytest.raises(ValueError, match="detumble_s is required"):
        mission_sequence_from_mapping({"template": "detumble_then_hold", "duration_s": 5.0})
