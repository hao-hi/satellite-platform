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
from satmodel.platform.project import PlatformProject
from satmodel.platform.records import ExperimentRecord, ExperimentSummary
from satmodel.platform.reporting import ReportBuilder
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
    "PlatformProject",
    "ReportBuilder",
    "experiment_plan_from_mapping",
    "load_experiment_plan",
    "set_mapping_path",
]
