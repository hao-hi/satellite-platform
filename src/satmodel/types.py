"""Shared data objects for satmodel components."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from satmodel.math import quat_angle_error_deg, quat_normalize


def _vec3(value, *, name: str) -> np.ndarray:
    arr = np.asarray(value, dtype=float).reshape(-1)
    if arr.size != 3:
        raise ValueError(f"{name} must contain three elements")
    return arr.copy()


def _quat(value, *, name: str) -> np.ndarray:
    arr = np.asarray(value, dtype=float).reshape(-1)
    if arr.size != 4:
        raise ValueError(f"{name} must contain four elements")
    return quat_normalize(arr)


@dataclass
class RigidBodyState:
    """Rigid-body attitude state with scalar-first quaternion."""

    quaternion: np.ndarray
    omega: np.ndarray
    time: float = 0.0

    def __post_init__(self):
        self.quaternion = _quat(self.quaternion, name="quaternion")
        self.omega = _vec3(self.omega, name="omega")
        self.time = float(self.time)

    def copy(self) -> "RigidBodyState":
        return RigidBodyState(self.quaternion.copy(), self.omega.copy(), self.time)


@dataclass
class ReferenceAttitude:
    """Desired attitude and optional desired body rate."""

    quaternion: np.ndarray = field(default_factory=lambda: np.array([1.0, 0.0, 0.0, 0.0]))
    omega: np.ndarray = field(default_factory=lambda: np.zeros(3))

    def __post_init__(self):
        self.quaternion = _quat(self.quaternion, name="reference quaternion")
        self.omega = _vec3(self.omega, name="reference omega")


@dataclass
class EnvironmentSample:
    """Environment values sampled for a state at one time."""

    gravity_gradient_torque: np.ndarray = field(default_factory=lambda: np.zeros(3))
    residual_magnetic_torque: np.ndarray = field(default_factory=lambda: np.zeros(3))
    aerodynamic_torque: np.ndarray = field(default_factory=lambda: np.zeros(3))
    solar_pressure_torque: np.ndarray = field(default_factory=lambda: np.zeros(3))
    position_eci: np.ndarray = field(default_factory=lambda: np.zeros(3))
    velocity_eci: np.ndarray = field(default_factory=lambda: np.zeros(3))
    magnetic_field_eci: np.ndarray = field(default_factory=lambda: np.zeros(3))
    density: float = 0.0
    eclipse: bool = False

    def __post_init__(self):
        for name in (
            "gravity_gradient_torque",
            "residual_magnetic_torque",
            "aerodynamic_torque",
            "solar_pressure_torque",
            "position_eci",
            "velocity_eci",
            "magnetic_field_eci",
        ):
            setattr(self, name, _vec3(getattr(self, name), name=name))
        self.density = float(self.density)
        self.eclipse = bool(self.eclipse)

    @property
    def total_torque(self) -> np.ndarray:
        return (
            self.gravity_gradient_torque
            + self.residual_magnetic_torque
            + self.aerodynamic_torque
            + self.solar_pressure_torque
        )


@dataclass
class SensorMeasurement:
    """Simplified attitude and gyro measurement packet."""

    time: float
    attitude: np.ndarray
    gyro: np.ndarray
    environment: EnvironmentSample | None = None

    def __post_init__(self):
        self.time = float(self.time)
        self.attitude = _quat(self.attitude, name="attitude measurement")
        self.gyro = _vec3(self.gyro, name="gyro measurement")


@dataclass
class EstimatedState:
    """State estimate presented to a controller."""

    quaternion: np.ndarray
    omega: np.ndarray
    gyro_bias: np.ndarray = field(default_factory=lambda: np.zeros(3))
    inertia_diag: np.ndarray | None = None
    covariance_ratio: float = np.nan
    physical_disturbance_torque: np.ndarray = field(default_factory=lambda: np.zeros(3))
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.quaternion = _quat(self.quaternion, name="estimated quaternion")
        self.omega = _vec3(self.omega, name="estimated omega")
        self.gyro_bias = _vec3(self.gyro_bias, name="gyro bias")
        if self.inertia_diag is not None:
            self.inertia_diag = _vec3(self.inertia_diag, name="inertia diagonal")
        self.physical_disturbance_torque = _vec3(
            self.physical_disturbance_torque,
            name="physical disturbance torque",
        )
        self.covariance_ratio = float(self.covariance_ratio)


@dataclass
class SimulationConfig:
    """Single-rate scenario settings."""

    duration: float = 8.0
    dt: float = 0.02
    seed: int = 0
    reference: ReferenceAttitude = field(default_factory=ReferenceAttitude)
    initial_state: RigidBodyState | None = None
    extra_disturbance: np.ndarray = field(default_factory=lambda: np.zeros(3))
    disturbance_noise_std: float = 0.0

    def __post_init__(self):
        self.duration = float(self.duration)
        self.dt = float(self.dt)
        if self.duration <= 0.0 or self.dt <= 0.0:
            raise ValueError("duration and dt must be positive")
        self.seed = int(self.seed)
        self.extra_disturbance = _vec3(self.extra_disturbance, name="extra disturbance")
        self.disturbance_noise_std = float(max(0.0, self.disturbance_noise_std))


@dataclass
class SimulationResult:
    """Time history returned by a scenario run."""

    time: np.ndarray
    true_quaternion: np.ndarray
    true_omega: np.ndarray
    estimated_quaternion: np.ndarray
    estimated_omega: np.ndarray
    commanded_torque: np.ndarray
    applied_torque: np.ndarray
    disturbance_torque: np.ndarray
    measured_attitude: np.ndarray
    measured_gyro: np.ndarray
    inertia_estimate: np.ndarray
    controller_disturbance_torque: np.ndarray
    reference_quaternion: np.ndarray = field(default_factory=lambda: np.array([1.0, 0.0, 0.0, 0.0]))
    estimator_diagnostics: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        self.reference_quaternion = _quat(self.reference_quaternion, name="result reference quaternion")

    @property
    def attitude_error_deg(self) -> np.ndarray:
        return np.asarray(
            [
                quat_angle_error_deg(q_ref, q_true)
                for q_ref, q_true in zip(self.reference_track(), self.true_quaternion)
            ],
            dtype=float,
        )

    def reference_track(self) -> np.ndarray:
        return np.broadcast_to(self.reference_quaternion, self.true_quaternion.shape)

    def errors_to(self, reference: ReferenceAttitude) -> np.ndarray:
        return np.asarray(
            [quat_angle_error_deg(reference.quaternion, q) for q in self.true_quaternion],
            dtype=float,
        )

    def metrics(self, reference: ReferenceAttitude | None = None) -> dict[str, float]:
        reference = ReferenceAttitude() if reference is None else reference
        err = self.errors_to(reference)
        torque_norm = np.linalg.norm(self.applied_torque, axis=1)
        integral = getattr(np, "trapezoid", np.trapz)
        return {
            "initial_error_deg": float(err[0]),
            "final_error_deg": float(err[-1]),
            "rms_error_deg": float(np.sqrt(np.mean(err**2))),
            "effort_nms": float(integral(torque_norm, self.time)),
            "peak_torque_nm": float(np.max(np.abs(self.applied_torque))),
        }
