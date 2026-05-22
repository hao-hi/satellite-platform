"""Quaternion and frame utilities used by satmodel."""

from __future__ import annotations

import numpy as np


def quat_normalize(q) -> np.ndarray:
    arr = np.asarray(q, dtype=float).reshape(4)
    norm = float(np.linalg.norm(arr))
    return arr.copy() if norm < 1e-15 else arr / norm


def quat_mul(q1, q2) -> np.ndarray:
    w1, x1, y1, z1 = np.asarray(q1, dtype=float).reshape(4)
    w2, x2, y2, z2 = np.asarray(q2, dtype=float).reshape(4)
    return np.array(
        [
            w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
            w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
            w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
        ],
        dtype=float,
    )


def quat_inv(q) -> np.ndarray:
    arr = np.asarray(q, dtype=float).reshape(4)
    return np.array([arr[0], -arr[1], -arr[2], -arr[3]], dtype=float) / (
        float(arr @ arr) + 1e-15
    )


def quat_from_axis_angle(axis, angle) -> np.ndarray:
    vec = np.asarray(axis, dtype=float).reshape(3)
    norm = float(np.linalg.norm(vec))
    if norm < 1e-15:
        return np.array([1.0, 0.0, 0.0, 0.0], dtype=float)
    unit = vec / norm
    half = 0.5 * float(angle)
    return np.array([np.cos(half), *(np.sin(half) * unit)], dtype=float)


def quat_from_omega(omega, dt) -> np.ndarray:
    rate = np.asarray(omega, dtype=float).reshape(3)
    magnitude = float(np.linalg.norm(rate))
    if magnitude < 1e-12:
        return np.array([1.0, 0.0, 0.0, 0.0], dtype=float)
    return quat_from_axis_angle(rate / magnitude, magnitude * float(dt))


def omega_matrix(omega) -> np.ndarray:
    wx, wy, wz = np.asarray(omega, dtype=float).reshape(3)
    return np.array(
        [
            [0.0, -wx, -wy, -wz],
            [wx, 0.0, wz, -wy],
            [wy, -wz, 0.0, wx],
            [wz, wy, -wx, 0.0],
        ],
        dtype=float,
    )


def quat_error(reference, current) -> np.ndarray:
    error = quat_mul(quat_inv(reference), current)
    return -error if error[0] < 0.0 else error


def quat_angle_error_deg(reference, current) -> float:
    ref = quat_normalize(reference)
    cur = quat_normalize(current)
    dot = float(np.clip(abs(ref @ cur), -1.0, 1.0))
    return float(np.rad2deg(2.0 * np.arccos(dot)))


def small_angle_from_quat(q) -> np.ndarray:
    dq = quat_normalize(q)
    if dq[0] < 0.0:
        dq = -dq
    return 2.0 * dq[1:]


def body_to_inertial_dcm(q) -> np.ndarray:
    w, x, y, z = quat_normalize(q)
    return np.array(
        [
            [1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - w * z), 2.0 * (x * z + w * y)],
            [2.0 * (x * y + w * z), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - w * x)],
            [2.0 * (x * z - w * y), 2.0 * (y * z + w * x), 1.0 - 2.0 * (x * x + y * y)],
        ],
        dtype=float,
    )


def inertial_to_body_dcm(q) -> np.ndarray:
    return body_to_inertial_dcm(q).T
