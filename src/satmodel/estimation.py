"""Attitude estimation and estimator-stack composition."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from satmodel.identification import (
    RLSIdentifier,
    TrackingDifferentiator,
    ensure_diag_inertia,
    reconstruct_disturbance_torque,
)
from satmodel.math import quat_from_omega, quat_inv, quat_mul, quat_normalize, small_angle_from_quat
from satmodel.types import EstimatedState, SensorMeasurement


@dataclass
class MEKFConfig:
    process_attitude: float = 1e-7
    process_bias: float = 1e-10
    measurement: float = 1e-5


class MEKF:
    """Multiplicative EKF for quaternion attitude and gyro bias."""

    def __init__(self, quaternion=None, bias=None, config: MEKFConfig | None = None):
        self.config = MEKFConfig() if config is None else config
        self.quaternion = quat_normalize(
            np.array([1.0, 0.0, 0.0, 0.0], dtype=float) if quaternion is None else quaternion
        )
        self.bias = np.zeros(3, dtype=float) if bias is None else np.asarray(bias, dtype=float).reshape(3)
        self.P = np.block([[1e-4 * np.eye(3), np.zeros((3, 3))], [np.zeros((3, 3)), 1e-6 * np.eye(3)]])
        self.Q = np.block(
            [
                [self.config.process_attitude * np.eye(3), np.zeros((3, 3))],
                [np.zeros((3, 3)), self.config.process_bias * np.eye(3)],
            ]
        )
        self.R = self.config.measurement * np.eye(3)
        self.last_residual = np.zeros(3, dtype=float)

    def reset(self, quaternion=None):
        self.quaternion = quat_normalize(
            np.array([1.0, 0.0, 0.0, 0.0], dtype=float) if quaternion is None else quaternion
        )
        self.bias = np.zeros(3, dtype=float)
        self.P = np.block([[1e-4 * np.eye(3), np.zeros((3, 3))], [np.zeros((3, 3)), 1e-6 * np.eye(3)]])
        self.last_residual = np.zeros(3, dtype=float)

    @staticmethod
    def _symmetrize(P) -> np.ndarray:
        P = 0.5 * (P + P.T)
        idx = np.diag_indices_from(P)
        P[idx] = np.maximum(P[idx], 1e-12)
        return P

    def predict(self, gyro, dt: float):
        gyro = np.asarray(gyro, dtype=float).reshape(3)
        dq = quat_from_omega(gyro - self.bias, dt)
        self.quaternion = quat_normalize(quat_mul(self.quaternion, dq))
        I = np.eye(3)
        transition = np.block([[I, -dt * I], [np.zeros((3, 3)), I]])
        self.P = self._symmetrize(transition @ self.P @ transition.T + self.Q * dt)

    def correct(self, attitude):
        residual = small_angle_from_quat(quat_mul(attitude, quat_inv(self.quaternion)))
        H = np.hstack([np.eye(3), np.zeros((3, 3))])
        S = H @ self.P @ H.T + self.R
        gain = np.linalg.solve(S.T, (self.P @ H.T).T).T
        dx = gain @ residual
        self.bias = self.bias + dx[3:]
        joseph_left = np.eye(6) - gain @ H
        self.P = self._symmetrize(joseph_left @ self.P @ joseph_left.T + gain @ self.R @ gain.T)
        correction = np.array([1.0, *(0.5 * dx[:3])], dtype=float)
        self.quaternion = quat_normalize(quat_mul(correction, self.quaternion))
        self.last_residual = residual.copy()

    def update(self, measurement: SensorMeasurement, applied_torque=None, dt: float = 0.01) -> EstimatedState:
        del applied_torque
        self.predict(measurement.gyro, dt)
        self.correct(measurement.attitude)
        return EstimatedState(
            quaternion=self.quaternion,
            omega=measurement.gyro - self.bias,
            gyro_bias=self.bias,
            diagnostics={"attitude_residual": self.last_residual.copy()},
        )


class EstimatorStack:
    """MEKF plus optional diagonal inertia identifier."""

    def __init__(
        self,
        mekf: MEKF | None = None,
        identifier: RLSIdentifier | None = None,
        differentiator: TrackingDifferentiator | None = None,
        nominal_inertia_diag=None,
        disturbance_alpha: float = 0.08,
    ):
        self.mekf = MEKF() if mekf is None else mekf
        self.identifier = identifier
        self.differentiator = TrackingDifferentiator() if differentiator is None else differentiator
        self.inertia_diag = ensure_diag_inertia(
            np.array([0.0417, 0.0833, 0.1083]) if nominal_inertia_diag is None else nominal_inertia_diag
        )
        self.disturbance_alpha = float(np.clip(disturbance_alpha, 1e-3, 1.0))
        self.disturbance = np.zeros(3, dtype=float)

    def reset(self, quaternion=None):
        self.mekf.reset(quaternion)
        self.differentiator.reset()
        self.disturbance[:] = 0.0
        if self.identifier is not None:
            self.identifier.reset()
            self.inertia_diag = self.identifier.inertia_diag.copy()

    def update(self, measurement: SensorMeasurement, applied_torque, dt: float) -> EstimatedState:
        attitude_state = self.mekf.update(measurement, applied_torque, dt)
        wdot = self.differentiator.update(attitude_state.omega, dt)
        disturbance_raw = reconstruct_disturbance_torque(
            attitude_state.omega,
            wdot,
            applied_torque,
            self.inertia_diag,
        )
        self.disturbance = (1.0 - self.disturbance_alpha) * self.disturbance + self.disturbance_alpha * disturbance_raw
        diagnostics = dict(attitude_state.diagnostics)
        diagnostics["wdot"] = wdot.copy()
        diagnostics["physical_disturbance_torque"] = self.disturbance.copy()
        covariance_ratio = np.nan
        if self.identifier is not None:
            self.inertia_diag, _ = self.identifier.update(
                attitude_state.omega,
                wdot,
                applied_torque,
                disturbance_torque=self.disturbance,
                dt=dt,
            )
            diagnostics.update(self.identifier.diagnostics())
            covariance_ratio = self.identifier.covariance_ratio()
        return EstimatedState(
            quaternion=attitude_state.quaternion,
            omega=attitude_state.omega,
            gyro_bias=attitude_state.gyro_bias,
            inertia_diag=self.inertia_diag.copy(),
            covariance_ratio=covariance_ratio,
            physical_disturbance_torque=self.disturbance.copy(),
            diagnostics=diagnostics,
        )
