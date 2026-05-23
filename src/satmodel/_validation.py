"""Small internal coercion helpers shared across satmodel components."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np


def vec3(value, *, name: str) -> np.ndarray:
    arr = np.asarray(value, dtype=float).reshape(-1)
    if arr.size != 3:
        raise ValueError(f"{name} must contain three elements")
    return arr.copy()


def mat3(value, *, name: str) -> np.ndarray:
    arr = np.asarray(value, dtype=float)
    if arr.shape == (3,):
        arr = np.diag(arr)
    if arr.shape != (3, 3):
        raise ValueError(f"{name} must be a 3-vector or a 3x3 matrix")
    return arr.copy()


def scalar_or_vec3(value, *, name: str) -> np.ndarray:
    arr = np.asarray(value, dtype=float).reshape(-1)
    if arr.size == 1:
        return np.full(3, float(arr[0]), dtype=float)
    if arr.size != 3:
        raise ValueError(f"{name} must be scalar or contain three elements")
    return arr.copy()


def unit_vec3(value, *, name: str = "direction", allow_zero: bool = False) -> np.ndarray:
    arr = vec3(value, name=name)
    norm = float(np.linalg.norm(arr))
    if norm < 1e-12:
        if allow_zero:
            return np.zeros(3, dtype=float)
        raise ValueError(f"{name} must be nonzero")
    return arr / norm


def utc_datetime(value: datetime, *, name: str = "epoch_utc") -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)
