"""Rigid-body attitude dynamics."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from satmodel._validation import mat3
from satmodel.math import body_to_inertial_dcm, omega_matrix, quat_normalize
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

    def __init__(self, inertia, *, inertia_provider=None, integrator=None, state_effector=None):
        self.base_inertia = self._coerce_inertia(inertia)
        self.inertia_provider = inertia_provider
        self.integrator = RK4Integrator() if integrator is None else integrator
        self.state_effector = state_effector

    @staticmethod
    def _coerce_inertia(inertia) -> np.ndarray:
        return mat3(inertia, name="inertia")

    def inertia_at(self, time: float, state: RigidBodyState | None = None) -> np.ndarray:
        if self.inertia_provider is None:
            return self.base_inertia.copy()
        return self._coerce_inertia(self.inertia_provider(float(time), state))

    def angular_acceleration(self, omega, torque, disturbance, inertia, wheel_momentum=None) -> np.ndarray:
        rate = np.asarray(omega, dtype=float).reshape(3)
        inertia = self._coerce_inertia(inertia)
        total = np.asarray(torque, dtype=float).reshape(3) + np.asarray(disturbance, dtype=float).reshape(3)
        wheel_momentum = (
            np.zeros(3, dtype=float) if wheel_momentum is None else np.asarray(wheel_momentum, dtype=float).reshape(3)
        )
        return np.linalg.solve(inertia, total - np.cross(rate, inertia @ rate + wheel_momentum))

    def hub_rotational_angular_momentum(self, state: RigidBodyState, *, frame: str = "body") -> np.ndarray:
        momentum = self.inertia_at(state.time, state) @ state.omega
        return self._express_momentum(momentum, state, frame)

    def hub_rotational_energy(self, state: RigidBodyState) -> float:
        inertia = self.inertia_at(state.time, state)
        return float(0.5 * state.omega @ inertia @ state.omega)

    def wheel_angular_momentum(self, state: RigidBodyState, *, frame: str = "body") -> np.ndarray:
        if self.state_effector is None or not hasattr(self.state_effector, "body_momentum"):
            return np.zeros(3, dtype=float)
        return self._express_momentum(self.state_effector.body_momentum(), state, frame)

    def wheel_rotational_energy(self) -> float:
        if self.state_effector is None or not hasattr(self.state_effector, "rotational_energy"):
            return 0.0
        return float(self.state_effector.rotational_energy())

    def total_rotational_angular_momentum(self, state: RigidBodyState, *, frame: str = "body") -> np.ndarray:
        momentum = self.inertia_at(state.time, state) @ state.omega
        if self.state_effector is not None and hasattr(self.state_effector, "body_momentum"):
            momentum = momentum + self.state_effector.body_momentum()
        return self._express_momentum(momentum, state, frame)

    @staticmethod
    def _express_momentum(momentum, state: RigidBodyState, frame: str) -> np.ndarray:
        if frame == "body":
            return np.asarray(momentum, dtype=float).reshape(3).copy()
        if frame == "inertial":
            return body_to_inertial_dcm(state.quaternion) @ np.asarray(momentum, dtype=float).reshape(3)
        raise ValueError("momentum frame must be 'body' or 'inertial'")

    def step(self, state: RigidBodyState, torque, disturbance=None, dt: float = 0.01) -> RigidBodyState:
        disturbance = np.zeros(3, dtype=float) if disturbance is None else np.asarray(disturbance, dtype=float)
        torque = np.asarray(torque, dtype=float).reshape(3)
        disturbance = disturbance.reshape(3)
        state_effector = self.state_effector
        effector_state = (
            np.empty(0, dtype=float)
            if state_effector is None or not hasattr(state_effector, "state_vector")
            else np.asarray(state_effector.state_vector(), dtype=float).reshape(-1)
        )
        y0 = np.concatenate([state.quaternion, state.omega, effector_state])

        def rhs(time, y):
            q = quat_normalize(y[:4])
            omega = y[4:7]
            inertia = self.inertia_at(time, RigidBodyState(q, omega, time))
            qdot = 0.5 * omega_matrix(omega) @ q
            coupled_state = y[7:]
            wheel_momentum = (
                np.zeros(3, dtype=float)
                if state_effector is None or not hasattr(state_effector, "body_momentum")
                else state_effector.body_momentum(coupled_state)
            )
            effector_dot = (
                np.empty(0, dtype=float)
                if state_effector is None or not hasattr(state_effector, "state_derivative")
                else np.asarray(state_effector.state_derivative(coupled_state), dtype=float).reshape(-1)
            )
            wdot = self.angular_acceleration(omega, torque, disturbance, inertia, wheel_momentum)
            return np.concatenate([qdot, wdot, effector_dot])

        y1 = self.integrator.step(rhs, state.time, y0, float(dt))
        if state_effector is not None and hasattr(state_effector, "set_state_vector"):
            state_effector.set_state_vector(y1[7:])
        return RigidBodyState(quat_normalize(y1[:4]), y1[4:7], state.time + float(dt))
