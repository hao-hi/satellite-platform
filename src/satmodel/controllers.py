"""Attitude controllers."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from satmodel._validation import scalar_or_vec3
from satmodel.math import quat_error
from satmodel.types import EstimatedState, ReferenceAttitude


class PDController:
    """Quaternion-error PD body-torque controller."""

    def __init__(self, kp: float = 2.0, kd: float = 0.5):
        self.kp = float(kp)
        self.kd = float(kd)
        self.last_error = np.array([1.0, 0.0, 0.0, 0.0], dtype=float)

    def reset(self):
        self.last_error = np.array([1.0, 0.0, 0.0, 0.0], dtype=float)

    def command(self, reference: ReferenceAttitude, estimate: EstimatedState, dt: float) -> np.ndarray:
        del dt
        self.last_error = quat_error(reference.quaternion, estimate.quaternion)
        rate_error = estimate.omega - reference.omega
        return -self.kp * self.last_error[1:] - self.kd * rate_error

    def disturbance_estimate_torque(self) -> np.ndarray:
        return np.zeros(3, dtype=float)


@dataclass
class LADRCConfig:
    """Bandwidth-parameterized LADRC settings."""

    omega_c: float = 4.5
    omega_o: float = 8.5
    b0: float | np.ndarray = 1.0
    b0_filter_alpha: float = 0.12
    adapt_b0_from_inertia: bool = True


class LADRCController:
    """Three-axis linear ADRC with quaternion-error feedback."""

    def __init__(self, config: LADRCConfig | None = None):
        self.config = LADRCConfig() if config is None else config
        self.b0 = scalar_or_vec3(self.config.b0, name="LADRC b0")
        self.b0 = np.maximum(self.b0, 1e-6)
        self.kp = float(self.config.omega_c**2)
        self.kd = float(2.0 * self.config.omega_c)
        self.beta1 = float(3.0 * self.config.omega_o)
        self.beta2 = float(3.0 * self.config.omega_o**2)
        self.beta3 = float(self.config.omega_o**3)
        self.reset()

    def reset(self):
        self.z1 = np.zeros(3, dtype=float)
        self.z2 = np.zeros(3, dtype=float)
        self.z3 = np.zeros(3, dtype=float)
        self.last_applied = np.zeros(3, dtype=float)
        self.last_error = np.array([1.0, 0.0, 0.0, 0.0], dtype=float)

    def _update_b0(self, estimate: EstimatedState):
        if not self.config.adapt_b0_from_inertia or estimate.inertia_diag is None:
            return
        target = 1.0 / np.maximum(np.asarray(estimate.inertia_diag, dtype=float), 1e-6)
        alpha = float(np.clip(self.config.b0_filter_alpha, 1e-3, 1.0))
        self.b0 = np.maximum((1.0 - alpha) * self.b0 + alpha * target, 1e-6)

    def _eso_step(self, measurement_error, dt: float):
        observer_error = self.z1 - np.asarray(measurement_error, dtype=float)
        z1_dot = self.z2 - self.beta1 * observer_error
        z2_dot = self.z3 - self.beta2 * observer_error + self.b0 * self.last_applied
        z3_dot = -self.beta3 * observer_error
        self.z1 = self.z1 + dt * z1_dot
        self.z2 = self.z2 + dt * z2_dot
        self.z3 = self.z3 + dt * z3_dot

    def command(self, reference: ReferenceAttitude, estimate: EstimatedState, dt: float) -> np.ndarray:
        self._update_b0(estimate)
        self.last_error = quat_error(reference.quaternion, estimate.quaternion)
        feedback_error = -self.last_error[1:]
        self._eso_step(feedback_error, float(dt))
        rate_error = estimate.omega - reference.omega
        return (self.kp * feedback_error - self.kd * rate_error - self.z3) / self.b0

    def observe_applied_torque(self, applied_torque):
        self.last_applied = np.asarray(applied_torque, dtype=float).reshape(3).copy()

    def disturbance_estimate_torque(self) -> np.ndarray:
        return (self.z3 / self.b0).copy()
