"""Shared data objects for satmodel components."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Any

import numpy as np

from satmodel._validation import utc_datetime, vec3
from satmodel.math import quat_angle_error_deg, quat_normalize


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
        self.omega = vec3(self.omega, name="omega")
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
        self.omega = vec3(self.omega, name="reference omega")


@dataclass
class OrbitState:
    """Cartesian inertial orbit state."""

    position_eci_m: np.ndarray
    velocity_eci_m_s: np.ndarray

    def __post_init__(self):
        self.position_eci_m = vec3(self.position_eci_m, name="orbit position")
        self.velocity_eci_m_s = vec3(self.velocity_eci_m_s, name="orbit velocity")


@dataclass
class GeodeticPoint:
    """WGS-84-like geodetic location consumed by Earth field backends."""

    latitude_deg: float = 0.0
    longitude_deg: float = 0.0
    altitude_m: float = 0.0

    def __post_init__(self):
        self.latitude_deg = float(self.latitude_deg)
        self.longitude_deg = float(self.longitude_deg)
        self.altitude_m = float(self.altitude_m)
        if not -90.0 <= self.latitude_deg <= 90.0:
            raise ValueError("geodetic latitude must be inside [-90, 90] deg")


def _default_epoch() -> datetime:
    return datetime(2000, 1, 1, tzinfo=timezone.utc)


@dataclass
class EnvironmentContext:
    """External field values sampled for one simulation time."""

    position_eci: np.ndarray = field(default_factory=lambda: np.zeros(3))
    velocity_eci: np.ndarray = field(default_factory=lambda: np.zeros(3))
    magnetic_field_eci: np.ndarray = field(default_factory=lambda: np.zeros(3))
    sun_vector_eci: np.ndarray = field(default_factory=lambda: np.zeros(3))
    density: float = 0.0
    eclipse: bool = False
    epoch_utc: datetime = field(default_factory=_default_epoch)
    geodetic: GeodeticPoint = field(default_factory=GeodeticPoint)

    def __post_init__(self):
        for name in (
            "position_eci",
            "velocity_eci",
            "magnetic_field_eci",
            "sun_vector_eci",
        ):
            setattr(self, name, vec3(getattr(self, name), name=name))
        self.density = float(self.density)
        self.eclipse = bool(self.eclipse)
        self.epoch_utc = utc_datetime(self.epoch_utc, name="environment epoch_utc")
        if not isinstance(self.geodetic, GeodeticPoint):
            self.geodetic = GeodeticPoint(**dict(self.geodetic))


@dataclass
class TorqueBudget:
    """Named body-frame torque contributions."""

    terms: dict[str, np.ndarray] = field(default_factory=dict)

    def __post_init__(self):
        self.terms = {
            str(name): vec3(torque, name=f"{name} torque")
            for name, torque in dict(self.terms).items()
        }

    @property
    def total_torque(self) -> np.ndarray:
        total = np.zeros(3, dtype=float)
        for torque in self.terms.values():
            total = total + torque
        return total


@dataclass
class WheelArrayTelemetry:
    """Reaction-wheel allocation and post-saturation telemetry."""

    body_command_nm: np.ndarray
    wheel_command_nm: np.ndarray
    wheel_torque_nm: np.ndarray
    body_torque_nm: np.ndarray
    allocation_error_nm: np.ndarray
    wheel_speed_rad_s: np.ndarray
    wheel_momentum_nms: np.ndarray
    wheel_momentum_capacity_nms: np.ndarray
    torque_saturated: np.ndarray
    speed_saturated: np.ndarray
    enabled: np.ndarray
    available_torque_lower_nm: np.ndarray
    available_torque_upper_nm: np.ndarray
    free_wheel_mask: np.ndarray
    allocation_mode: str
    rank_after_failures: int
    requested_body_torque_nm: np.ndarray
    achievable_body_torque_nm: np.ndarray

    def __post_init__(self):
        self.body_command_nm = vec3(self.body_command_nm, name="wheel-array body command")
        self.body_torque_nm = vec3(self.body_torque_nm, name="wheel-array body torque")
        self.allocation_error_nm = vec3(self.allocation_error_nm, name="wheel-array allocation error")
        self.requested_body_torque_nm = vec3(self.requested_body_torque_nm, name="requested body torque")
        self.achievable_body_torque_nm = vec3(self.achievable_body_torque_nm, name="achievable body torque")
        self.wheel_command_nm = np.asarray(self.wheel_command_nm, dtype=float).reshape(-1).copy()
        self.wheel_torque_nm = np.asarray(self.wheel_torque_nm, dtype=float).reshape(-1).copy()
        self.wheel_speed_rad_s = np.asarray(self.wheel_speed_rad_s, dtype=float).reshape(-1).copy()
        self.wheel_momentum_nms = np.asarray(self.wheel_momentum_nms, dtype=float).reshape(-1).copy()
        self.wheel_momentum_capacity_nms = np.asarray(self.wheel_momentum_capacity_nms, dtype=float).reshape(-1).copy()
        self.torque_saturated = np.asarray(self.torque_saturated, dtype=bool).reshape(-1).copy()
        self.speed_saturated = np.asarray(self.speed_saturated, dtype=bool).reshape(-1).copy()
        self.enabled = np.asarray(self.enabled, dtype=bool).reshape(-1).copy()
        self.available_torque_lower_nm = np.asarray(self.available_torque_lower_nm, dtype=float).reshape(-1).copy()
        self.available_torque_upper_nm = np.asarray(self.available_torque_upper_nm, dtype=float).reshape(-1).copy()
        self.free_wheel_mask = np.asarray(self.free_wheel_mask, dtype=bool).reshape(-1).copy()
        self.allocation_mode = str(self.allocation_mode)
        self.rank_after_failures = int(self.rank_after_failures)
        widths = {
            self.wheel_command_nm.size,
            self.wheel_torque_nm.size,
            self.wheel_speed_rad_s.size,
            self.wheel_momentum_nms.size,
            self.wheel_momentum_capacity_nms.size,
            self.torque_saturated.size,
            self.speed_saturated.size,
            self.enabled.size,
            self.available_torque_lower_nm.size,
            self.available_torque_upper_nm.size,
            self.free_wheel_mask.size,
        }
        if len(widths) != 1:
            raise ValueError("reaction-wheel telemetry arrays must have the same length")


@dataclass
class SensorMeasurement:
    """Simplified attitude and gyro measurement packet."""

    time: float
    attitude: np.ndarray
    gyro: np.ndarray
    environment: EnvironmentContext | None = None

    def __post_init__(self):
        self.time = float(self.time)
        self.attitude = _quat(self.attitude, name="attitude measurement")
        self.gyro = vec3(self.gyro, name="gyro measurement")


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
        self.omega = vec3(self.omega, name="estimated omega")
        self.gyro_bias = vec3(self.gyro_bias, name="gyro bias")
        if self.inertia_diag is not None:
            self.inertia_diag = vec3(self.inertia_diag, name="inertia diagonal")
        self.physical_disturbance_torque = vec3(
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
        self.extra_disturbance = vec3(self.extra_disturbance, name="extra disturbance")
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
    disturbance_torque_terms: dict[str, np.ndarray]
    measured_attitude: np.ndarray
    measured_gyro: np.ndarray
    inertia_estimate: np.ndarray
    controller_disturbance_torque: np.ndarray
    reference_quaternion: np.ndarray = field(default_factory=lambda: np.array([1.0, 0.0, 0.0, 0.0]))
    estimator_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    actuator_telemetry: list[Any] = field(default_factory=list)

    def __post_init__(self):
        self.reference_quaternion = _quat(self.reference_quaternion, name="result reference quaternion")
        self.disturbance_torque_terms = {
            str(name): np.asarray(track, dtype=float).reshape(-1, 3)
            for name, track in dict(self.disturbance_torque_terms).items()
        }

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
        metrics = {
            "initial_error_deg": float(err[0]),
            "final_error_deg": float(err[-1]),
            "rms_error_deg": float(np.sqrt(np.mean(err**2))),
            "effort_nms": float(integral(torque_norm, self.time)),
            "peak_torque_nm": float(np.max(np.abs(self.applied_torque))),
        }
        disturbance_norm = np.linalg.norm(self.disturbance_torque, axis=1)
        if disturbance_norm.size:
            metrics["peak_disturbance_torque_nm"] = float(np.max(disturbance_norm))
            metrics["mean_disturbance_torque_nm"] = float(np.mean(disturbance_norm))
        for name, track in self.disturbance_torque_terms.items():
            term_track = np.asarray(track, dtype=float)
            if term_track.size == 0:
                continue
            term_norm = np.linalg.norm(term_track, axis=1)
            safe = re.sub(r"[^a-zA-Z0-9_]+", "_", str(name)).strip("_") or "disturbance"
            metrics[f"peak_{safe}_torque_nm"] = float(np.max(term_norm))
            metrics[f"mean_{safe}_torque_nm"] = float(np.mean(term_norm))
        wheel_speeds = self.wheel_speeds_rad_s
        if wheel_speeds.size:
            speed_norm = np.linalg.norm(wheel_speeds, axis=1)
            metrics["peak_wheel_speed_rad_s"] = float(np.max(np.abs(wheel_speeds)))
            metrics["final_wheel_speed_norm_rad_s"] = float(speed_norm[-1])
            metrics["mean_wheel_speed_norm_rad_s"] = float(np.mean(speed_norm))
        wheel_momentum = self.wheel_momentum_nms
        wheel_capacity = self.wheel_momentum_capacity_nms
        if wheel_momentum.size and wheel_capacity.size:
            fraction = np.abs(wheel_momentum) / np.maximum(np.abs(wheel_capacity), 1e-12)
            metrics["peak_wheel_momentum_fraction"] = float(np.max(fraction))
        allocation_error = self.wheel_allocation_error_nm
        if allocation_error.size:
            allocation_norm = np.linalg.norm(allocation_error, axis=1)
            metrics["peak_allocation_error_nm"] = float(np.max(allocation_norm))
        return metrics

    def _wheel_track(self, name: str) -> np.ndarray:
        if not self.actuator_telemetry:
            return np.empty((0, 0), dtype=float)
        track = [getattr(item, name) for item in self.actuator_telemetry if item is not None and hasattr(item, name)]
        return np.asarray(track) if track else np.empty((0, 0), dtype=float)

    @property
    def wheel_speeds_rad_s(self) -> np.ndarray:
        return self._wheel_track("wheel_speed_rad_s")

    @property
    def wheel_torques_nm(self) -> np.ndarray:
        return self._wheel_track("wheel_torque_nm")

    @property
    def wheel_torque_commands_nm(self) -> np.ndarray:
        return self._wheel_track("wheel_command_nm")

    @property
    def wheel_momentum_nms(self) -> np.ndarray:
        return self._wheel_track("wheel_momentum_nms")

    @property
    def wheel_momentum_capacity_nms(self) -> np.ndarray:
        return self._wheel_track("wheel_momentum_capacity_nms")

    @property
    def wheel_allocation_error_nm(self) -> np.ndarray:
        return self._wheel_track("allocation_error_nm")

    @property
    def wheel_saturation_flags(self) -> np.ndarray:
        torque = self._wheel_track("torque_saturated")
        speed = self._wheel_track("speed_saturated")
        if torque.size == 0:
            return np.empty((0, 0), dtype=bool)
        return np.logical_or(torque.astype(bool), speed.astype(bool))

    @property
    def wheel_available_torque_lower_nm(self) -> np.ndarray:
        return self._wheel_track("available_torque_lower_nm")

    @property
    def wheel_available_torque_upper_nm(self) -> np.ndarray:
        return self._wheel_track("available_torque_upper_nm")

    @property
    def wheel_free_masks(self) -> np.ndarray:
        return self._wheel_track("free_wheel_mask")
