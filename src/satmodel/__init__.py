"""Composable satellite attitude models and methods."""

from satmodel.actuators import TorqueActuator, TorqueActuatorConfig
from satmodel.controllers import LADRCConfig, LADRCController, PDController
from satmodel.dynamics import RK4Integrator, SpacecraftDynamics
from satmodel.environment import EnvironmentModel, LEOEnvironment, LEOEnvironmentConfig, ZeroEnvironment
from satmodel.estimation import EstimatorStack, MEKF, MEKFConfig
from satmodel.identification import (
    RLSConfig,
    RLSIdentifier,
    TrackingDifferentiator,
    build_inertia_regression_matrix,
)
from satmodel.optimization import (
    GridSearchOptimizer,
    NelderMeadOptimizer,
    OptimizationResult,
    PSOOptimizer,
    RandomSearchOptimizer,
    SimulatedAnnealingOptimizer,
)
from satmodel.sensors import AttitudeSensor, GyroSensor, SensorSuite
from satmodel.system import SatelliteSystem, ScenarioRunner, build_default_system
from satmodel.types import (
    EnvironmentSample,
    EstimatedState,
    ReferenceAttitude,
    RigidBodyState,
    SensorMeasurement,
    SimulationConfig,
    SimulationResult,
)

__all__ = [
    "AttitudeSensor",
    "EnvironmentModel",
    "EnvironmentSample",
    "EstimatedState",
    "EstimatorStack",
    "GyroSensor",
    "GridSearchOptimizer",
    "LADRCConfig",
    "LADRCController",
    "LEOEnvironment",
    "LEOEnvironmentConfig",
    "MEKF",
    "MEKFConfig",
    "NelderMeadOptimizer",
    "OptimizationResult",
    "PDController",
    "PSOOptimizer",
    "RK4Integrator",
    "RandomSearchOptimizer",
    "RLSConfig",
    "RLSIdentifier",
    "ReferenceAttitude",
    "RigidBodyState",
    "SatelliteSystem",
    "ScenarioRunner",
    "SensorMeasurement",
    "SensorSuite",
    "SimulationConfig",
    "SimulationResult",
    "SimulatedAnnealingOptimizer",
    "SpacecraftDynamics",
    "TorqueActuator",
    "TorqueActuatorConfig",
    "TrackingDifferentiator",
    "ZeroEnvironment",
    "build_default_system",
    "build_inertia_regression_matrix",
]
