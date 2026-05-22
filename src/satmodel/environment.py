"""Engineering environment models for low-Earth-orbit attitude simulations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from satmodel.math import inertial_to_body_dcm
from satmodel.types import EnvironmentSample, RigidBodyState


class EnvironmentModel(Protocol):
    """Environment contract consumed by the scenario runner."""

    def sample(self, time: float, state: RigidBodyState, inertia: np.ndarray) -> EnvironmentSample:
        ...


def _normalize(value) -> np.ndarray:
    arr = np.asarray(value, dtype=float).reshape(3)
    norm = float(np.linalg.norm(arr))
    return np.zeros(3, dtype=float) if norm < 1e-12 else arr / norm


def _rot1(angle) -> np.ndarray:
    c, s = np.cos(angle), np.sin(angle)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]], dtype=float)


def _rot3(angle) -> np.ndarray:
    c, s = np.cos(angle), np.sin(angle)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]], dtype=float)


def projected_box_area(size_m, direction_body) -> float:
    lx, ly, lz = np.asarray(size_m, dtype=float).reshape(3)
    nx, ny, nz = np.abs(_normalize(direction_body))
    return float(ly * lz * nx + lx * lz * ny + lx * ly * nz)


@dataclass
class LEOEnvironmentConfig:
    """Minimal circular-LEO environment settings."""

    altitude_m: float = 400e3
    inclination_deg: float = 51.6
    raan_deg: float = 25.0
    arglat0_deg: float = 40.0
    size_m: np.ndarray = field(default_factory=lambda: np.array([0.10, 0.20, 0.30]))
    drag_coefficient: float = 2.2
    aerodynamic_cp_m: np.ndarray = field(default_factory=lambda: np.array([0.015, -0.008, 0.012]))
    density_400_kg_m3: float = 4.0e-12
    density_scale_height_m: float = 55e3
    srp_reflectivity: float = 1.4
    srp_cp_m: np.ndarray = field(default_factory=lambda: np.array([-0.012, 0.010, 0.018]))
    sun_vector_eci: np.ndarray = field(default_factory=lambda: np.array([1.0, 0.25, 0.10]))
    solar_pressure_n_m2: float = 4.56e-6
    residual_dipole_am2: np.ndarray = field(default_factory=lambda: np.array([0.015, -0.010, 0.012]))
    earth_dipole_am2: float = 7.94e22
    mu_earth: float = 3.986004418e14
    earth_radius_m: float = 6378.137e3
    earth_rotation_rad_s: float = 7.2921159e-5
    mu0_over_4pi: float = 1.0e-7


class LEOEnvironment:
    """Circular-orbit environment with four attitude disturbance torques."""

    def __init__(self, config: LEOEnvironmentConfig | None = None):
        self.config = LEOEnvironmentConfig() if config is None else config
        cfg = self.config
        self.radius = float(cfg.earth_radius_m + cfg.altitude_m)
        self.mean_motion = float(np.sqrt(cfg.mu_earth / self.radius**3))
        self.inclination = np.deg2rad(float(cfg.inclination_deg))
        self.raan = np.deg2rad(float(cfg.raan_deg))
        self.arglat0 = np.deg2rad(float(cfg.arglat0_deg))
        self.sun_hat = _normalize(cfg.sun_vector_eci)

    def orbit_state(self, time: float) -> tuple[np.ndarray, np.ndarray]:
        cfg = self.config
        arglat = self.arglat0 + self.mean_motion * float(time)
        r_pf = self.radius * np.array([np.cos(arglat), np.sin(arglat), 0.0], dtype=float)
        v_pf = np.sqrt(cfg.mu_earth / self.radius) * np.array([-np.sin(arglat), np.cos(arglat), 0.0])
        frame = _rot3(self.raan) @ _rot1(self.inclination)
        return frame @ r_pf, frame @ v_pf

    def magnetic_field_eci(self, position_eci) -> np.ndarray:
        cfg = self.config
        radius = max(float(np.linalg.norm(position_eci)), 1e-9)
        radius_hat = np.asarray(position_eci, dtype=float) / radius
        dipole = np.array([0.0, 0.0, cfg.earth_dipole_am2], dtype=float)
        return cfg.mu0_over_4pi / radius**3 * (3.0 * radius_hat * (dipole @ radius_hat) - dipole)

    def density(self, altitude_m: float) -> float:
        cfg = self.config
        return float(cfg.density_400_kg_m3 * np.exp(-(altitude_m - 400e3) / cfg.density_scale_height_m))

    def eclipsed(self, position_eci) -> bool:
        cfg = self.config
        if float(np.asarray(position_eci) @ self.sun_hat) >= 0.0:
            return False
        radial = np.asarray(position_eci) - (np.asarray(position_eci) @ self.sun_hat) * self.sun_hat
        return bool(np.linalg.norm(radial) <= cfg.earth_radius_m)

    def sample(self, time: float, state: RigidBodyState, inertia: np.ndarray) -> EnvironmentSample:
        cfg = self.config
        position, velocity = self.orbit_state(time)
        radius_hat = _normalize(position)
        field_eci = self.magnetic_field_eci(position)
        density = self.density(float(np.linalg.norm(position)) - cfg.earth_radius_m)
        eclipse = self.eclipsed(position)
        body_from_eci = inertial_to_body_dcm(state.quaternion)
        radius_body = body_from_eci @ radius_hat
        field_body = body_from_eci @ field_eci
        v_rel_eci = velocity - np.cross(np.array([0.0, 0.0, cfg.earth_rotation_rad_s]), position)
        v_rel_body = body_from_eci @ v_rel_eci
        sun_body = body_from_eci @ self.sun_hat
        inertia = np.asarray(inertia, dtype=float).reshape(3, 3)

        tau_gg = 3.0 * cfg.mu_earth / max(np.linalg.norm(position), 1e-9) ** 3 * np.cross(
            radius_body,
            inertia @ radius_body,
        )
        tau_res = np.cross(np.asarray(cfg.residual_dipole_am2, dtype=float), field_body)
        drag_area = projected_box_area(cfg.size_m, v_rel_body)
        drag_force = -0.5 * density * cfg.drag_coefficient * drag_area * np.linalg.norm(v_rel_body) * v_rel_body
        tau_aero = np.cross(np.asarray(cfg.aerodynamic_cp_m, dtype=float), drag_force)
        srp_force = np.zeros(3, dtype=float)
        if not eclipse:
            srp_force = (
                -cfg.solar_pressure_n_m2
                * cfg.srp_reflectivity
                * projected_box_area(cfg.size_m, sun_body)
                * _normalize(sun_body)
            )
        tau_srp = np.cross(np.asarray(cfg.srp_cp_m, dtype=float), srp_force)
        return EnvironmentSample(
            gravity_gradient_torque=tau_gg,
            residual_magnetic_torque=tau_res,
            aerodynamic_torque=tau_aero,
            solar_pressure_torque=tau_srp,
            position_eci=position,
            velocity_eci=velocity,
            magnetic_field_eci=field_eci,
            density=density,
            eclipse=eclipse,
        )


class ZeroEnvironment:
    """Environment useful for open-loop and unit-test scenarios."""

    def sample(self, time: float, state: RigidBodyState, inertia: np.ndarray) -> EnvironmentSample:
        del time, state, inertia
        return EnvironmentSample()
