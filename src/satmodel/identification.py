"""Diagonal inertia identification and angular-acceleration helpers."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def ensure_diag_inertia(value, minimum: float = 1e-6) -> np.ndarray:
    arr = np.asarray(value, dtype=float)
    if arr.shape == (3, 3):
        arr = np.diag(arr)
    arr = arr.reshape(-1)
    if arr.size != 3:
        raise ValueError("inertia diagonal must contain three elements")
    return np.maximum(arr[:3], float(minimum))


def build_inertia_regression_matrix(omega, wdot) -> np.ndarray:
    wx, wy, wz = np.asarray(omega, dtype=float).reshape(3)
    ax, ay, az = np.asarray(wdot, dtype=float).reshape(3)
    return np.array(
        [
            [ax, -wy * wz, wy * wz],
            [wx * wz, ay, -wx * wz],
            [-wx * wy, wx * wy, az],
        ],
        dtype=float,
    )


def reconstruct_disturbance_torque(omega, wdot, applied_torque, inertia_diag) -> np.ndarray:
    omega = np.asarray(omega, dtype=float).reshape(3)
    wdot = np.asarray(wdot, dtype=float).reshape(3)
    applied = np.asarray(applied_torque, dtype=float).reshape(3)
    inertia = ensure_diag_inertia(inertia_diag)
    return inertia * wdot - applied + np.cross(omega, inertia * omega)


class TrackingDifferentiator:
    """Second-order angular-rate differentiator."""

    def __init__(self, omega_n: float = 8.0, zeta: float = 1.0, max_acc: float | None = 15.0):
        self.omega_n = float(omega_n)
        self.zeta = float(zeta)
        self.max_acc = None if max_acc is None else float(max_acc)
        self.reset()

    def reset(self):
        self.x1 = np.zeros(3, dtype=float)
        self.x2 = np.zeros(3, dtype=float)
        self.initialized = False

    def update(self, value, dt: float) -> np.ndarray:
        x = np.asarray(value, dtype=float).reshape(3)
        if not self.initialized:
            self.x1 = x.copy()
            self.initialized = True
            return self.x2.copy()
        x1_prev, x2_prev = self.x1.copy(), self.x2.copy()
        self.x1 = x1_prev + dt * x2_prev
        x2_dot = -(self.omega_n**2) * (x1_prev - x) - 2.0 * self.zeta * self.omega_n * x2_prev
        if self.max_acc is not None:
            x2_dot = np.clip(x2_dot, -self.max_acc, self.max_acc)
        self.x2 = x2_prev + dt * x2_dot
        return self.x2.copy()


@dataclass
class RLSConfig:
    """Fading-memory RLS settings for diagonal inertia."""

    initial_inertia_diag: tuple[float, float, float] = (0.035, 0.075, 0.095)
    lambda_factor: float = 0.985
    update_gain: float = 0.18
    covariance_scale: float = 0.5
    measurement_noise: float = 0.03
    filter_alpha: float = 0.16
    disturbance_blend: float = 0.2
    min_inertia: float = 1e-4
    max_inertia: float = 2.0
    max_step: float = 2e-3
    excitation_floor: float = 1e-6


class RLSIdentifier:
    """Fading-memory diagonal inertia RLS with bounded updates."""

    def __init__(self, config: RLSConfig | None = None):
        self.config = RLSConfig() if config is None else config
        self.reset()

    def reset(self):
        cfg = self.config
        self.inertia_diag = ensure_diag_inertia(cfg.initial_inertia_diag, minimum=cfg.min_inertia)
        self._initial = self.inertia_diag.copy()
        self.P = np.eye(3, dtype=float) * float(cfg.covariance_scale)
        self.P0_trace = float(np.trace(self.P))
        self.phi_filtered = np.zeros((3, 3), dtype=float)
        self.y_filtered = np.zeros(3, dtype=float)
        self.last = {
            "rls_updated": 0.0,
            "rls_excitation": 0.0,
            "rls_residual_rms": np.nan,
            "rls_covariance_trace": float(np.trace(self.P)),
        }

    def covariance_ratio(self) -> float:
        return float(np.trace(self.P) / max(self.P0_trace, 1e-12))

    def diagnostics(self) -> dict[str, float]:
        return dict(self.last)

    def update(self, omega, wdot, applied_torque, disturbance_torque=None, dt: float | None = None):
        del dt
        cfg = self.config
        disturbance = np.zeros(3, dtype=float) if disturbance_torque is None else np.asarray(disturbance_torque, dtype=float)
        phi = build_inertia_regression_matrix(omega, wdot)
        y = np.asarray(applied_torque, dtype=float).reshape(3) + cfg.disturbance_blend * disturbance.reshape(3)
        alpha = float(np.clip(cfg.filter_alpha, 1e-3, 1.0))
        self.phi_filtered = (1.0 - alpha) * self.phi_filtered + alpha * phi
        self.y_filtered = (1.0 - alpha) * self.y_filtered + alpha * y
        excitation = float(np.linalg.norm(self.phi_filtered))
        innovation = self.y_filtered - self.phi_filtered @ self.inertia_diag
        self.last = {
            "rls_updated": 0.0,
            "rls_excitation": excitation,
            "rls_residual_rms": float(np.sqrt(np.mean(innovation**2))),
            "rls_covariance_trace": float(np.trace(self.P)),
        }
        if excitation < cfg.excitation_floor:
            return self.inertia_diag.copy(), False
        lambda_eff = float(np.clip(cfg.lambda_factor, 0.9, 1.0))
        P_pred = 0.5 * (self.P / lambda_eff + (self.P / lambda_eff).T)
        R = (cfg.measurement_noise**2) * np.eye(3)
        S = self.phi_filtered @ P_pred @ self.phi_filtered.T + R
        gain = P_pred @ self.phi_filtered.T @ np.linalg.pinv(S)
        candidate = self.inertia_diag + cfg.update_gain * (gain @ innovation)
        candidate = np.clip(candidate, self.inertia_diag - cfg.max_step, self.inertia_diag + cfg.max_step)
        candidate = np.clip(candidate, cfg.min_inertia, cfg.max_inertia)
        left = np.eye(3) - gain @ self.phi_filtered
        joseph = left @ P_pred @ left.T + gain @ R @ gain.T
        self.P = 0.5 * (joseph + joseph.T)
        diag = np.diag_indices(3)
        self.P[diag] = np.maximum(self.P[diag], 1e-12)
        updated = bool(np.linalg.norm(candidate - self.inertia_diag) > 1e-15)
        self.inertia_diag = candidate
        self.last["rls_updated"] = float(updated)
        self.last["rls_covariance_trace"] = float(np.trace(self.P))
        return self.inertia_diag.copy(), updated
