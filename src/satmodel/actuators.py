"""Actuator abstractions for ideal body torques and reaction-wheel arrays."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from satmodel._validation import scalar_or_vec3, unit_vec3
from satmodel.types import WheelArrayTelemetry


def _coerce_wheel_vector(value, count: int, *, name: str) -> np.ndarray:
    arr = np.asarray(value, dtype=float).reshape(-1)
    if arr.size == 1:
        return np.full(count, float(arr[0]), dtype=float)
    if arr.size != count:
        raise ValueError(f"{name} must be scalar or contain one value per wheel")
    return arr.copy()


@dataclass
class TorqueActuatorConfig:
    torque_limit_nm: float | np.ndarray = 0.2


class TorqueActuator:
    """Body-axis torque actuator with saturation and applied-torque memory."""

    def __init__(self, config: TorqueActuatorConfig | None = None):
        self.config = TorqueActuatorConfig() if config is None else config
        self.limit = scalar_or_vec3(self.config.torque_limit_nm, name="torque limits")
        if np.any(self.limit <= 0.0):
            raise ValueError("torque limits must be positive and scalar or length three")
        self.last_command = np.zeros(3, dtype=float)
        self.last_applied = np.zeros(3, dtype=float)

    def reset(self):
        self.last_command = np.zeros(3, dtype=float)
        self.last_applied = np.zeros(3, dtype=float)

    def apply(self, command, dt: float | None = None) -> np.ndarray:
        del dt
        self.last_command = np.asarray(command, dtype=float).reshape(3).copy()
        self.last_applied = np.clip(self.last_command, -self.limit, self.limit)
        return self.last_applied.copy()


@dataclass
class ReactionWheelConfig:
    """Single wheel parameters measured around its spin axis."""

    axis_body: np.ndarray
    spin_inertia_kgm2: float = 2.6e-5
    max_torque_nm: float = 0.007
    max_speed_rad_s: float = 8000.0 * 2.0 * np.pi / 60.0
    initial_speed_rad_s: float = 0.0
    enabled: bool = True

    def __post_init__(self):
        self.axis_body = unit_vec3(self.axis_body, name="reaction-wheel axis")
        self.spin_inertia_kgm2 = float(self.spin_inertia_kgm2)
        self.max_torque_nm = float(self.max_torque_nm)
        self.max_speed_rad_s = float(self.max_speed_rad_s)
        self.initial_speed_rad_s = float(self.initial_speed_rad_s)
        self.enabled = bool(self.enabled)
        if self.spin_inertia_kgm2 <= 0.0:
            raise ValueError("reaction-wheel spin inertia must be positive")
        if self.max_torque_nm <= 0.0 or self.max_speed_rad_s <= 0.0:
            raise ValueError("reaction-wheel torque and speed limits must be positive")
        if abs(self.initial_speed_rad_s) > self.max_speed_rad_s:
            raise ValueError("reaction-wheel initial speed must fit inside the speed limit")


class _ReactionWheel:
    """Spin-axis wheel model with torque and wheel-speed saturation."""

    def __init__(self, config: ReactionWheelConfig):
        self.config = config
        self.reset()

    def reset(self):
        self.speed_rad_s = float(self.config.initial_speed_rad_s)
        self.last_torque_nm = 0.0
        self.torque_saturated = False
        self.speed_saturated = abs(self.speed_rad_s) >= self.config.max_speed_rad_s

    @property
    def enabled(self) -> bool:
        return bool(self.config.enabled)

    @property
    def momentum_capacity_nms(self) -> float:
        return float(self.config.spin_inertia_kgm2 * self.config.max_speed_rad_s)

    def set_speed(self, speed_rad_s: float):
        clipped = float(np.clip(speed_rad_s, -self.config.max_speed_rad_s, self.config.max_speed_rad_s))
        self.speed_saturated = not np.isclose(clipped, speed_rad_s) or abs(clipped) >= self.config.max_speed_rad_s
        self.speed_rad_s = clipped

    def limit_motor_torque(self, command_nm: float, dt: float | None) -> float:
        request = float(command_nm)
        duration = 0.0 if dt is None else float(dt)
        if duration < 0.0:
            raise ValueError("reaction-wheel time step must be non-negative")
        if not self.enabled:
            self.last_torque_nm = 0.0
            self.torque_saturated = abs(request) > 0.0
            self.speed_saturated = abs(self.speed_rad_s) >= self.config.max_speed_rad_s
            return 0.0

        limited = float(np.clip(request, -self.config.max_torque_nm, self.config.max_torque_nm))
        self.torque_saturated = not np.isclose(limited, request)
        self.speed_saturated = abs(self.speed_rad_s) >= self.config.max_speed_rad_s
        actual = limited
        if duration > 0.0:
            candidate = self.speed_rad_s + duration * actual / self.config.spin_inertia_kgm2
            clipped_speed = float(np.clip(candidate, -self.config.max_speed_rad_s, self.config.max_speed_rad_s))
            if abs(clipped_speed) >= self.config.max_speed_rad_s or np.isclose(
                abs(clipped_speed),
                self.config.max_speed_rad_s,
            ):
                self.speed_saturated = True
            if not np.isclose(candidate, clipped_speed):
                self.speed_saturated = True
                actual = (clipped_speed - self.speed_rad_s) * self.config.spin_inertia_kgm2 / duration
        self.last_torque_nm = float(actual)
        return self.last_torque_nm


@dataclass
class ReactionWheelArrayConfig:
    """Reaction-wheel allocation model with bounded multi-wheel modes."""

    wheels: tuple[ReactionWheelConfig, ...]
    allocation: str = "bounded_pinv"
    momentum_reference_rad_s: float | tuple[float, ...] | np.ndarray = 0.0
    momentum_gain: float = 0.0
    wheel_torque_weights: float | tuple[float, ...] | np.ndarray = 1.0

    def __post_init__(self):
        self.wheels = tuple(self.wheels)
        if len(self.wheels) < 3:
            raise ValueError("reaction-wheel arrays need at least three wheels")
        self.allocation = str(self.allocation)
        if self.allocation not in {"pinv", "bounded_pinv", "nullspace_momentum"}:
            raise ValueError("reaction-wheel allocation must be 'pinv', 'bounded_pinv', or 'nullspace_momentum'")
        if np.linalg.matrix_rank(self.axis_matrix) < 3:
            raise ValueError("reaction-wheel axes must span body torque space")
        self.momentum_reference_rad_s = _coerce_wheel_vector(
            self.momentum_reference_rad_s,
            len(self.wheels),
            name="momentum reference",
        )
        self.momentum_gain = float(self.momentum_gain)
        if self.momentum_gain < 0.0:
            raise ValueError("momentum gain must be non-negative")
        self.wheel_torque_weights = _coerce_wheel_vector(
            self.wheel_torque_weights,
            len(self.wheels),
            name="wheel torque weights",
        )
        if np.any(self.wheel_torque_weights <= 0.0):
            raise ValueError("wheel torque weights must be positive")

    @property
    def axis_matrix(self) -> np.ndarray:
        return np.column_stack([wheel.axis_body for wheel in self.wheels])

    @classmethod
    def orthogonal_3wheel(
        cls,
        *,
        spin_inertia_kgm2: float = 2.6e-5,
        max_torque_nm: float = 0.007,
        max_speed_rad_s: float = 8000.0 * 2.0 * np.pi / 60.0,
        allocation: str = "bounded_pinv",
        momentum_reference_rad_s: float | tuple[float, ...] | np.ndarray = 0.0,
        momentum_gain: float = 0.0,
        wheel_torque_weights: float | tuple[float, ...] | np.ndarray = 1.0,
    ) -> "ReactionWheelArrayConfig":
        return cls(
            tuple(
                ReactionWheelConfig(axis, spin_inertia_kgm2, max_torque_nm, max_speed_rad_s)
                for axis in np.eye(3)
            ),
            allocation=allocation,
            momentum_reference_rad_s=momentum_reference_rad_s,
            momentum_gain=momentum_gain,
            wheel_torque_weights=wheel_torque_weights,
        )

    @classmethod
    def pyramid_4wheel(
        cls,
        *,
        spin_inertia_kgm2: float = 2.6e-5,
        max_torque_nm: float = 0.007,
        max_speed_rad_s: float = 8000.0 * 2.0 * np.pi / 60.0,
        allocation: str = "bounded_pinv",
        momentum_reference_rad_s: float | tuple[float, ...] | np.ndarray = 0.0,
        momentum_gain: float = 0.0,
        wheel_torque_weights: float | tuple[float, ...] | np.ndarray = 1.0,
    ) -> "ReactionWheelArrayConfig":
        axes = np.array(
            [
                [1.0, 1.0, 1.0],
                [1.0, -1.0, -1.0],
                [-1.0, 1.0, -1.0],
                [-1.0, -1.0, 1.0],
            ],
            dtype=float,
        )
        return cls(
            tuple(
                ReactionWheelConfig(axis, spin_inertia_kgm2, max_torque_nm, max_speed_rad_s)
                for axis in axes
            ),
            allocation=allocation,
            momentum_reference_rad_s=momentum_reference_rad_s,
            momentum_gain=momentum_gain,
            wheel_torque_weights=wheel_torque_weights,
        )


class ReactionWheelStateEffector:
    """Reaction-wheel array whose wheel speeds are propagated with the spacecraft."""

    def __init__(self, config: ReactionWheelArrayConfig | None = None):
        self.config = ReactionWheelArrayConfig.pyramid_4wheel() if config is None else config
        self.wheels = [_ReactionWheel(item) for item in self.config.wheels]
        self.last_command = np.zeros(3, dtype=float)
        self.last_applied = np.zeros(3, dtype=float)
        self._last_free_wheel_mask = np.asarray([wheel.enabled for wheel in self.wheels], dtype=bool)
        self.last_telemetry = self._telemetry(np.zeros(len(self.wheels)), np.zeros(len(self.wheels)))

    @property
    def axis_matrix(self) -> np.ndarray:
        return np.column_stack([wheel.config.axis_body for wheel in self.wheels])

    def reset(self):
        for wheel in self.wheels:
            wheel.reset()
        self.last_command = np.zeros(3, dtype=float)
        self.last_applied = np.zeros(3, dtype=float)
        self._last_free_wheel_mask = np.asarray([wheel.enabled for wheel in self.wheels], dtype=bool)
        self.last_telemetry = self._telemetry(np.zeros(len(self.wheels)), np.zeros(len(self.wheels)))

    def disable_wheel(self, index: int):
        self.wheels[int(index)].config.enabled = False

    def enable_wheel(self, index: int):
        self.wheels[int(index)].config.enabled = True

    def _enabled_axes(self) -> tuple[np.ndarray, np.ndarray]:
        enabled = np.asarray([wheel.enabled for wheel in self.wheels], dtype=bool)
        return enabled, self.axis_matrix[:, enabled]

    def _available_torque_bounds(self, dt: float | None) -> tuple[np.ndarray, np.ndarray]:
        duration = 0.0 if dt is None else float(dt)
        if duration < 0.0:
            raise ValueError("reaction-wheel time step must be non-negative")
        lower, upper = [], []
        for wheel in self.wheels:
            if not wheel.enabled:
                lower.append(0.0)
                upper.append(0.0)
                continue
            cfg = wheel.config
            lo = -cfg.max_torque_nm
            hi = cfg.max_torque_nm
            if duration > 0.0:
                speed_lo = cfg.spin_inertia_kgm2 * (-cfg.max_speed_rad_s - wheel.speed_rad_s) / duration
                speed_hi = cfg.spin_inertia_kgm2 * (cfg.max_speed_rad_s - wheel.speed_rad_s) / duration
                lo = max(lo, speed_lo)
                hi = min(hi, speed_hi)
            if lo > hi:
                midpoint = 0.5 * (lo + hi)
                lo = hi = midpoint
            lower.append(float(lo))
            upper.append(float(hi))
        return np.asarray(lower, dtype=float), np.asarray(upper, dtype=float)

    def _rank_after_failures(self) -> int:
        _, axes = self._enabled_axes()
        return 0 if axes.size == 0 else int(np.linalg.matrix_rank(axes))

    def _target_wheel_torque(self) -> np.ndarray:
        if self.config.allocation != "nullspace_momentum" or self.config.momentum_gain <= 0.0:
            return np.zeros(len(self.wheels), dtype=float)
        speeds = self.state_vector()
        reference = np.asarray(self.config.momentum_reference_rad_s, dtype=float)
        return self.config.momentum_gain * self.spin_inertia * (reference - speeds)

    @staticmethod
    def _weighted_target_solution(axes, rhs, target, weights) -> np.ndarray:
        if axes.size == 0:
            return np.empty(0, dtype=float)
        inverse_weights = 1.0 / np.asarray(weights, dtype=float)
        weighted_axes_t = inverse_weights[:, None] * axes.T
        correction_rhs = np.asarray(rhs, dtype=float).reshape(3) - axes @ target
        correction = weighted_axes_t @ (np.linalg.pinv(axes @ weighted_axes_t) @ correction_rhs)
        return target + correction

    def _bounded_allocate(self, body_command, lower, upper) -> tuple[np.ndarray, np.ndarray]:
        rhs = -np.asarray(body_command, dtype=float).reshape(3)
        enabled, _ = self._enabled_axes()
        wheel_torque = np.clip(self._target_wheel_torque(), lower, upper)
        free = enabled.copy()
        weights = np.asarray(self.config.wheel_torque_weights, dtype=float)
        target = self._target_wheel_torque()
        for _ in range(len(self.wheels) + 1):
            fixed = ~free
            residual = rhs - self.axis_matrix[:, fixed] @ wheel_torque[fixed]
            if np.any(free):
                wheel_torque[free] = self._weighted_target_solution(
                    self.axis_matrix[:, free],
                    residual,
                    target[free],
                    weights[free],
                )
            low_violation = free & (wheel_torque < lower - 1e-12)
            high_violation = free & (wheel_torque > upper + 1e-12)
            if not np.any(low_violation | high_violation):
                break
            wheel_torque[low_violation] = lower[low_violation]
            wheel_torque[high_violation] = upper[high_violation]
            free[low_violation | high_violation] = False
        return np.clip(wheel_torque, lower, upper), free

    def allocate(self, body_command, dt: float | None = None) -> np.ndarray:
        command = np.asarray(body_command, dtype=float).reshape(3)
        lower, upper = self._available_torque_bounds(dt)
        enabled, axes = self._enabled_axes()
        wheel_command = np.zeros(len(self.wheels), dtype=float)
        if self.config.allocation == "pinv":
            self._last_free_wheel_mask = enabled.copy()
            if axes.size and np.linalg.matrix_rank(axes) >= 3:
                # Positive motor torque increases wheel momentum, so the body sees the opposite torque.
                wheel_command[enabled] = -np.linalg.pinv(axes) @ command
            return wheel_command
        if axes.size and np.linalg.matrix_rank(axes) > 0:
            # Positive motor torque increases wheel momentum, so the body sees the opposite torque.
            wheel_command, free = self._bounded_allocate(command, lower, upper)
            self._last_free_wheel_mask = free
        else:
            self._last_free_wheel_mask = np.zeros(len(self.wheels), dtype=bool)
        return wheel_command

    def _telemetry(self, wheel_command, wheel_torque, dt: float | None = None) -> WheelArrayTelemetry:
        lower, upper = self._available_torque_bounds(dt)
        return WheelArrayTelemetry(
            body_command_nm=self.last_command,
            wheel_command_nm=wheel_command,
            wheel_torque_nm=wheel_torque,
            body_torque_nm=self.last_applied,
            allocation_error_nm=self.last_command - self.last_applied,
            wheel_speed_rad_s=[wheel.speed_rad_s for wheel in self.wheels],
            wheel_momentum_nms=[wheel.config.spin_inertia_kgm2 * wheel.speed_rad_s for wheel in self.wheels],
            wheel_momentum_capacity_nms=[wheel.momentum_capacity_nms for wheel in self.wheels],
            torque_saturated=[wheel.torque_saturated for wheel in self.wheels],
            speed_saturated=[wheel.speed_saturated for wheel in self.wheels],
            enabled=[wheel.enabled for wheel in self.wheels],
            available_torque_lower_nm=lower,
            available_torque_upper_nm=upper,
            free_wheel_mask=self._last_free_wheel_mask,
            allocation_mode=self.config.allocation,
            rank_after_failures=self._rank_after_failures(),
            requested_body_torque_nm=self.last_command,
            achievable_body_torque_nm=self.last_applied,
        )

    def state_vector(self) -> np.ndarray:
        return np.asarray([wheel.speed_rad_s for wheel in self.wheels], dtype=float)

    def set_state_vector(self, wheel_speed_rad_s):
        for wheel, speed in zip(self.wheels, np.asarray(wheel_speed_rad_s, dtype=float).reshape(-1)):
            wheel.set_speed(float(speed))
        wheel_command = np.asarray(self.last_telemetry.wheel_command_nm, dtype=float)
        wheel_torque = np.asarray([wheel.last_torque_nm for wheel in self.wheels], dtype=float)
        self.last_telemetry = self._telemetry(wheel_command, wheel_torque)

    @property
    def spin_inertia(self) -> np.ndarray:
        return np.asarray([wheel.config.spin_inertia_kgm2 for wheel in self.wheels], dtype=float)

    def state_derivative(self, wheel_speed_rad_s=None) -> np.ndarray:
        del wheel_speed_rad_s
        return np.asarray([wheel.last_torque_nm for wheel in self.wheels], dtype=float) / self.spin_inertia

    def body_momentum(self, wheel_speed_rad_s=None) -> np.ndarray:
        speeds = self.state_vector() if wheel_speed_rad_s is None else np.asarray(wheel_speed_rad_s, dtype=float).reshape(-1)
        return self.axis_matrix @ (self.spin_inertia * speeds)

    def rotational_energy(self, wheel_speed_rad_s=None) -> float:
        speeds = self.state_vector() if wheel_speed_rad_s is None else np.asarray(wheel_speed_rad_s, dtype=float).reshape(-1)
        return float(0.5 * np.sum(self.spin_inertia * speeds**2))

    def apply(self, command, dt: float | None = None) -> np.ndarray:
        self.last_command = np.asarray(command, dtype=float).reshape(3).copy()
        wheel_command = self.allocate(self.last_command, dt)
        wheel_torque = np.asarray(
            [wheel.limit_motor_torque(torque, dt) for wheel, torque in zip(self.wheels, wheel_command)],
            dtype=float,
        )
        self.last_applied = -(self.axis_matrix @ wheel_torque)
        self.last_telemetry = self._telemetry(wheel_command, wheel_torque, dt)
        return self.last_applied.copy()
