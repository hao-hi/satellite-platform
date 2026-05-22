"""Actuator abstractions."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class TorqueActuatorConfig:
    torque_limit_nm: float | np.ndarray = 0.2


class TorqueActuator:
    """Body-axis torque actuator with saturation and applied-torque memory."""

    def __init__(self, config: TorqueActuatorConfig | None = None):
        self.config = TorqueActuatorConfig() if config is None else config
        limit = np.asarray(self.config.torque_limit_nm, dtype=float).reshape(-1)
        self.limit = np.full(3, float(limit[0])) if limit.size == 1 else limit[:3].copy()
        if self.limit.size != 3 or np.any(self.limit <= 0.0):
            raise ValueError("torque limits must be positive and scalar or length three")
        self.last_command = np.zeros(3, dtype=float)
        self.last_applied = np.zeros(3, dtype=float)

    def apply(self, command, dt: float | None = None) -> np.ndarray:
        del dt
        self.last_command = np.asarray(command, dtype=float).reshape(3).copy()
        self.last_applied = np.clip(self.last_command, -self.limit, self.limit)
        return self.last_applied.copy()
