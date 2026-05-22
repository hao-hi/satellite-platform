"""Rigid-body attitude dynamics."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from satmodel.math import omega_matrix, quat_normalize
from satmodel.types import RigidBodyState


class RK4Integrator:
    """Fixed-step fourth-order Runge-Kutta integrator."""

    def step(self, rhs: Callable[[float, np.ndarray], np.ndarray], time: float, y: np.ndarray, dt: float) -> np.ndarray:
        k1 = rhs(time, y)
        k2 = rhs(time + 0.5 * dt, y + 0.5 * dt * k1)
        k3 = rhs(time + 0.5 * dt, y + 0.5 * dt * k2)
        k4 = rhs(time + dt, y + dt * k3)
        return y + (dt / 6.0) * (k1 + 2.0 * k2 + 2.0 * k3 + k4)


class SpacecraftDynamics:
    """Quaternion rigid-body propagation with replaceable inertia provider."""

    def __init__(self, inertia, *, inertia_provider=None, integrator=None):
        self.base_inertia = self._coerce_inertia(inertia)
        self.inertia_provider = inertia_provider
        self.integrator = RK4Integrator() if integrator is None else integrator

    @staticmethod
    def _coerce_inertia(inertia) -> np.ndarray:
        arr = np.asarray(inertia, dtype=float)
        if arr.shape == (3,):
            arr = np.diag(arr)
        if arr.shape != (3, 3):
            raise ValueError("inertia must be a 3-vector or a 3x3 matrix")
        return arr.copy()

    def inertia_at(self, time: float, state: RigidBodyState | None = None) -> np.ndarray:
        if self.inertia_provider is None:
            return self.base_inertia.copy()
        return self._coerce_inertia(self.inertia_provider(float(time), state))

    def angular_acceleration(self, omega, torque, disturbance, inertia) -> np.ndarray:
        rate = np.asarray(omega, dtype=float).reshape(3)
        inertia = self._coerce_inertia(inertia)
        total = np.asarray(torque, dtype=float).reshape(3) + np.asarray(disturbance, dtype=float).reshape(3)
        return np.linalg.solve(inertia, total - np.cross(rate, inertia @ rate))

    def step(self, state: RigidBodyState, torque, disturbance=None, dt: float = 0.01) -> RigidBodyState:
        disturbance = np.zeros(3, dtype=float) if disturbance is None else np.asarray(disturbance, dtype=float)
        torque = np.asarray(torque, dtype=float).reshape(3)
        disturbance = disturbance.reshape(3)
        y0 = np.concatenate([state.quaternion, state.omega])

        def rhs(time, y):
            q = quat_normalize(y[:4])
            omega = y[4:]
            inertia = self.inertia_at(time, RigidBodyState(q, omega, time))
            qdot = 0.5 * omega_matrix(omega) @ q
            wdot = self.angular_acceleration(omega, torque, disturbance, inertia)
            return np.concatenate([qdot, wdot])

        y1 = self.integrator.step(rhs, state.time, y0, float(dt))
        return RigidBodyState(quat_normalize(y1[:4]), y1[4:], state.time + float(dt))
