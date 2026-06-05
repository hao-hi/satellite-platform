"""Compatibility exports for the platform package.

New code should import from satmodel.platform or the focused platform modules.
"""

from satmodel.platform.plan import (
    ExperimentMonteCarloSpec,
    ExperimentOutputSpec,
    ExperimentPlan,
    ExperimentSweepSpec,
    experiment_plan_from_mapping,
    load_experiment_plan,
)
from satmodel.platform.mission import (
    MissionSequence,
    MissionStep,
    ModeTimeline,
    mission_sequence_from_mapping,
    mission_step_from_mapping,
)
from satmodel.platform.project import PlatformProject
from satmodel.platform.records import ExperimentRecord, ExperimentSummary
from satmodel.platform.reporting import ReportBuilder
from satmodel.platform.runtime import (
    RuntimeModule,
    RuntimeProcess,
    RuntimeTask,
    runtime_module_from_mapping,
    runtime_process_from_mapping,
    runtime_task_from_mapping,
)
from satmodel.platform.runner import ExperimentRunner
from satmodel.platform.utils import set_mapping_path

__all__ = [
    "ExperimentMonteCarloSpec",
    "ExperimentOutputSpec",
    "ExperimentPlan",
    "ExperimentRecord",
    "ExperimentRunner",
    "ExperimentSummary",
    "ExperimentSweepSpec",
    "MissionSequence",
    "MissionStep",
    "ModeTimeline",
    "PlatformProject",
    "ReportBuilder",
    "RuntimeModule",
    "RuntimeProcess",
    "RuntimeTask",
    "experiment_plan_from_mapping",
    "load_experiment_plan",
    "mission_sequence_from_mapping",
    "mission_step_from_mapping",
    "runtime_module_from_mapping",
    "runtime_process_from_mapping",
    "runtime_task_from_mapping",
    "set_mapping_path",
]
