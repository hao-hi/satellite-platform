"""High-level satellite system assembly and single-rate scenario runner."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from satmodel.actuators import TorqueActuator, TorqueActuatorConfig
from satmodel.controllers import LADRCConfig, LADRCController, PDController
from satmodel.dynamics import SpacecraftDynamics
from satmodel.environment import EnvironmentModel, LEOEnvironment
from satmodel.estimation import EstimatorStack, MEKF
from satmodel.identification import RLSIdentifier
from satmodel.math import quat_from_axis_angle
from satmodel.sensors import SensorSuite
from satmodel.types import (
    EstimatedState,
    ReferenceAttitude,
    RigidBodyState,
    SimulationConfig,
    SimulationResult,
)


ASTERIA_LIKE_INERTIA = np.array(
    [
        [0.0417, 0.0012, -0.0008],
        [0.0012, 0.0833, 0.0016],
        [-0.0008, 0.0016, 0.1083],
    ],
    dtype=float,
)


@dataclass
class StepRecord:
    state: RigidBodyState
    estimate: EstimatedState
    measurement: object
    commanded_torque: np.ndarray
    applied_torque: np.ndarray
    disturbance_torque: np.ndarray
    controller_disturbance_torque: np.ndarray


class SatelliteSystem:
    """Composable front door for an attitude-control simulation stack."""

    def __init__(
        self,
        dynamics: SpacecraftDynamics,
        environment: EnvironmentModel,
        sensors: SensorSuite,
        actuator: TorqueActuator,
        estimator,
        controller=None,
    ):
        self.dynamics = dynamics
        self.environment = environment
        self.sensors = sensors
        self.actuator = actuator
        self.estimator = estimator
        self.controller = controller

    def reset(self, initial_state: RigidBodyState, seed: int | None = None):
        self.sensors.reset(seed)
        if hasattr(self.estimator, "reset"):
            self.estimator.reset(initial_state.quaternion)
        if hasattr(self.controller, "reset"):
            self.controller.reset()
        self.actuator.apply(np.zeros(3, dtype=float))

    def step(
        self,
        state: RigidBodyState,
        reference: ReferenceAttitude,
        dt: float,
        *,
        extra_disturbance=None,
        disturbance_noise=None,
    ) -> StepRecord:
        extra = np.zeros(3, dtype=float) if extra_disturbance is None else np.asarray(extra_disturbance, dtype=float).reshape(3)
        noise = np.zeros(3, dtype=float) if disturbance_noise is None else np.asarray(disturbance_noise, dtype=float).reshape(3)
        inertia = self.dynamics.inertia_at(state.time, state)
        environment_sample = self.environment.sample(state.time, state, inertia)
        measurement = self.sensors.measure(state, environment_sample, state.time)
        estimate = self.estimator.update(measurement, self.actuator.last_applied, dt)
        command = (
            np.zeros(3, dtype=float)
            if self.controller is None
            else np.asarray(self.controller.command(reference, estimate, dt), dtype=float).reshape(3)
        )
        applied = self.actuator.apply(command, dt)
        if hasattr(self.controller, "observe_applied_torque"):
            self.controller.observe_applied_torque(applied)
        disturbance = environment_sample.total_torque + extra + noise
        controller_disturbance = (
            np.zeros(3, dtype=float)
            if self.controller is None or not hasattr(self.controller, "disturbance_estimate_torque")
            else np.asarray(self.controller.disturbance_estimate_torque(), dtype=float).reshape(3)
        )
        return StepRecord(
            state=self.dynamics.step(state, applied, disturbance, dt),
            estimate=estimate,
            measurement=measurement,
            commanded_torque=command,
            applied_torque=applied,
            disturbance_torque=disturbance,
            controller_disturbance_torque=controller_disturbance,
        )


class ScenarioRunner:
    """Single-rate fixed-step scenario runner."""

    def __init__(self, system: SatelliteSystem):
        self.system = system

    def run(self, config: SimulationConfig) -> SimulationResult:
        rng = np.random.RandomState(config.seed + 101)
        state = default_initial_state() if config.initial_state is None else config.initial_state.copy()
        self.system.reset(state, config.seed)
        steps = max(1, int(np.ceil(config.duration / config.dt)))

        true_q, true_w = [], []
        est_q, est_w = [], []
        command, applied, disturbance = [], [], []
        measured_attitude, measured_gyro, inertia = [], [], []
        controller_disturbance, diagnostics = [], []
        time = []
        for _ in range(steps):
            record = self.system.step(
                state,
                config.reference,
                config.dt,
                extra_disturbance=config.extra_disturbance,
                disturbance_noise=config.disturbance_noise_std * rng.randn(3),
            )
            time.append(state.time)
            true_q.append(state.quaternion.copy())
            true_w.append(state.omega.copy())
            est_q.append(record.estimate.quaternion.copy())
            est_w.append(record.estimate.omega.copy())
            command.append(record.commanded_torque.copy())
            applied.append(record.applied_torque.copy())
            disturbance.append(record.disturbance_torque.copy())
            measured_attitude.append(record.measurement.attitude.copy())
            measured_gyro.append(record.measurement.gyro.copy())
            inertia.append(
                np.full(3, np.nan, dtype=float)
                if record.estimate.inertia_diag is None
                else record.estimate.inertia_diag.copy()
            )
            controller_disturbance.append(record.controller_disturbance_torque.copy())
            diagnostics.append(dict(record.estimate.diagnostics))
            state = record.state
        return SimulationResult(
            time=np.asarray(time, dtype=float),
            true_quaternion=np.asarray(true_q, dtype=float),
            true_omega=np.asarray(true_w, dtype=float),
            estimated_quaternion=np.asarray(est_q, dtype=float),
            estimated_omega=np.asarray(est_w, dtype=float),
            commanded_torque=np.asarray(command, dtype=float),
            applied_torque=np.asarray(applied, dtype=float),
            disturbance_torque=np.asarray(disturbance, dtype=float),
            measured_attitude=np.asarray(measured_attitude, dtype=float),
            measured_gyro=np.asarray(measured_gyro, dtype=float),
            inertia_estimate=np.asarray(inertia, dtype=float),
            controller_disturbance_torque=np.asarray(controller_disturbance, dtype=float),
            reference_quaternion=config.reference.quaternion.copy(),
            estimator_diagnostics=diagnostics,
        )


def default_initial_state() -> RigidBodyState:
    axis = np.array([1.0, -0.6, 0.8], dtype=float)
    return RigidBodyState(
        quaternion=quat_from_axis_angle(axis, np.deg2rad(35.0)),
        omega=np.array([0.10, 0.08, -0.06], dtype=float),
    )


def build_default_system(
    *,
    controller: str | object | None = "pd",
    identify_inertia: bool = False,
    environment: EnvironmentModel | None = None,
) -> SatelliteSystem:
    """Build a runnable ASTERIA-like stack with replaceable components."""

    dynamics = SpacecraftDynamics(ASTERIA_LIKE_INERTIA)
    environment = LEOEnvironment() if environment is None else environment
    sensors = SensorSuite()
    actuator = TorqueActuator(TorqueActuatorConfig(0.2))
    estimator = EstimatorStack(
        mekf=MEKF(),
        identifier=RLSIdentifier() if identify_inertia else None,
        nominal_inertia_diag=np.diag(ASTERIA_LIKE_INERTIA),
    )
    controller_obj = controller
    if controller == "pd":
        controller_obj = PDController()
    elif controller == "ladrc":
        controller_obj = LADRCController(LADRCConfig(b0=1.0 / np.diag(ASTERIA_LIKE_INERTIA)))
    elif controller in (None, "open_loop"):
        controller_obj = None
    return SatelliteSystem(dynamics, environment, sensors, actuator, estimator, controller_obj)
