"""Simplified attitude and gyro sensor models."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from satmodel.math import quat_from_axis_angle, quat_mul, quat_normalize
from satmodel.types import EnvironmentContext, RigidBodyState, SensorMeasurement


def _random_unit(rng: np.random.RandomState) -> np.ndarray:
    vec = rng.randn(3)
    norm = float(np.linalg.norm(vec))
    return np.array([1.0, 0.0, 0.0], dtype=float) if norm < 1e-12 else vec / norm


@dataclass
class AttitudeSensor:
    """Attitude packet made by perturbing the true quaternion with small-angle noise."""

    noise_std_rad: float = 6e-4
    rng: np.random.RandomState = field(default_factory=lambda: np.random.RandomState(0))

    def reset(self, seed: int | None = None):
        self.rng = np.random.RandomState(seed)

    def measure(self, state: RigidBodyState) -> np.ndarray:
        dq = quat_from_axis_angle(_random_unit(self.rng), self.noise_std_rad * self.rng.randn())
        return quat_normalize(quat_mul(state.quaternion, dq))


@dataclass
class GyroSensor:
    """Gyroscope model with fixed initial bias, bias walk, and white noise."""

    noise_std_rad_s: float = 1e-3
    bias_std_rad_s: float = 2e-3
    bias_rw_scale: float = 0.02
    rng: np.random.RandomState = field(default_factory=lambda: np.random.RandomState(1))
    bias: np.ndarray = field(default_factory=lambda: np.zeros(3))

    def reset(self, seed: int | None = None):
        self.rng = np.random.RandomState(seed)
        self.bias = self.bias_std_rad_s * np.array([1.0, -0.7, 0.4], dtype=float)

    def measure(self, state: RigidBodyState) -> np.ndarray:
        self.bias = self.bias + self.bias_rw_scale * self.bias_std_rad_s * self.rng.randn(3)
        return state.omega + self.bias + self.noise_std_rad_s * self.rng.randn(3)


class SensorSuite:
    """Synchronized first-version sensor suite."""

    def __init__(self, attitude: AttitudeSensor | None = None, gyro: GyroSensor | None = None):
        self.attitude = AttitudeSensor() if attitude is None else attitude
        self.gyro = GyroSensor() if gyro is None else gyro

    def reset(self, seed: int | None = None):
        self.attitude.reset(seed)
        self.gyro.reset(None if seed is None else seed + 1)

    def measure(self, state: RigidBodyState, environment_context: EnvironmentContext | None, time: float) -> SensorMeasurement:
        return SensorMeasurement(
            time=time,
            attitude=self.attitude.measure(state),
            gyro=self.gyro.measure(state),
            environment=environment_context,
        )
