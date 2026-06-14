"""Environmental disturbance torque effectors for attitude simulations."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from satmodel._validation import unit_vec3, vec3
from satmodel.geometry import BoxGeometry
from satmodel.math import inertial_to_body_dcm
from satmodel.types import EnvironmentContext, RigidBodyState, TorqueBudget

class DisturbanceEffector(Protocol):
    """Torque effector driven by a rigid-body state and sampled environment."""

    def torque(self, state: RigidBodyState, inertia: np.ndarray, context: EnvironmentContext) -> np.ndarray:
        ...


@dataclass
class GravityGradientTorqueConfig:
    mu_earth: float = 3.986004418e14


class GravityGradientTorque:
    """Rigid-body gravity-gradient torque."""

    def __init__(self, config: GravityGradientTorqueConfig | None = None):
        self.config = GravityGradientTorqueConfig() if config is None else config

    def torque(self, state: RigidBodyState, inertia: np.ndarray, context: EnvironmentContext) -> np.ndarray:
        position = np.asarray(context.position_eci, dtype=float).reshape(3)
        radius = float(np.linalg.norm(position))
        if radius < 1e-9:
            return np.zeros(3, dtype=float)
        radius_body = inertial_to_body_dcm(state.quaternion) @ (position / radius)
        inertia = np.asarray(inertia, dtype=float).reshape(3, 3)
        return 3.0 * self.config.mu_earth / radius**3 * np.cross(radius_body, inertia @ radius_body)


@dataclass
class ResidualMagneticTorqueConfig:
    residual_dipole_am2: np.ndarray = field(default_factory=lambda: np.array([0.015, -0.010, 0.012]))

    def __post_init__(self):
        self.residual_dipole_am2 = vec3(self.residual_dipole_am2, name="residual dipole")


class ResidualMagneticTorque:
    """Torque from a body-fixed residual dipole in the sampled magnetic field."""

    def __init__(self, config: ResidualMagneticTorqueConfig | None = None):
        self.config = ResidualMagneticTorqueConfig() if config is None else config

    def torque(self, state: RigidBodyState, inertia: np.ndarray, context: EnvironmentContext) -> np.ndarray:
        del inertia
        field_body = inertial_to_body_dcm(state.quaternion) @ context.magnetic_field_eci
        return np.cross(self.config.residual_dipole_am2, field_body)


@dataclass
class AerodynamicTorqueConfig:
    drag_coefficient: float = 2.2
    aerodynamic_cp_m: np.ndarray = field(default_factory=lambda: np.array([0.015, -0.008, 0.012]))
    earth_rotation_rad_s: float = 7.2921159e-5

    def __post_init__(self):
        self.aerodynamic_cp_m = vec3(self.aerodynamic_cp_m, name="aerodynamic center of pressure")
        self.drag_coefficient = float(self.drag_coefficient)
        self.earth_rotation_rad_s = float(self.earth_rotation_rad_s)


class AerodynamicTorque:
    """Projected-box aerodynamic drag torque."""

    def __init__(self, geometry: BoxGeometry, config: AerodynamicTorqueConfig | None = None):
        self.geometry = geometry
        self.config = AerodynamicTorqueConfig() if config is None else config

    def torque(self, state: RigidBodyState, inertia: np.ndarray, context: EnvironmentContext) -> np.ndarray:
        del inertia
        cfg = self.config
        position = np.asarray(context.position_eci, dtype=float).reshape(3)
        velocity = np.asarray(context.velocity_eci, dtype=float).reshape(3)
        v_rel_eci = velocity - np.cross(np.array([0.0, 0.0, cfg.earth_rotation_rad_s]), position)
        v_rel_body = inertial_to_body_dcm(state.quaternion) @ v_rel_eci
        drag_area = self.geometry.projected_area(v_rel_body)
        drag_force = -0.5 * context.density * cfg.drag_coefficient * drag_area * np.linalg.norm(v_rel_body) * v_rel_body
        return np.cross(cfg.aerodynamic_cp_m, drag_force)


@dataclass
class SolarPressureTorqueConfig:
    srp_reflectivity: float = 1.4
    srp_cp_m: np.ndarray = field(default_factory=lambda: np.array([-0.012, 0.010, 0.018]))
    solar_pressure_n_m2: float = 4.56e-6

    def __post_init__(self):
        self.srp_cp_m = vec3(self.srp_cp_m, name="solar-pressure center of pressure")
        self.srp_reflectivity = float(self.srp_reflectivity)
        self.solar_pressure_n_m2 = float(self.solar_pressure_n_m2)


class SolarPressureTorque:
    """Projected-box solar radiation pressure torque."""

    def __init__(self, geometry: BoxGeometry, config: SolarPressureTorqueConfig | None = None):
        self.geometry = geometry
        self.config = SolarPressureTorqueConfig() if config is None else config

    def torque(self, state: RigidBodyState, inertia: np.ndarray, context: EnvironmentContext) -> np.ndarray:
        del inertia
        if context.eclipse:
            return np.zeros(3, dtype=float)
        cfg = self.config
        sun_body = inertial_to_body_dcm(state.quaternion) @ context.sun_vector_eci
        srp_force = (
            -cfg.solar_pressure_n_m2
            * cfg.srp_reflectivity
            * self.geometry.projected_area(sun_body)
            * unit_vec3(sun_body, allow_zero=True)
        )
        return np.cross(cfg.srp_cp_m, srp_force)


class DisturbanceEffectorSet:
    """Named dynamic effectors evaluated against one environment context."""

    def __init__(self, effectors: Iterable[tuple[str, DisturbanceEffector]]):
        self.effectors = tuple((str(name), effector) for name, effector in effectors)
        if len({name for name, _ in self.effectors}) != len(self.effectors):
            raise ValueError("disturbance effector names must be unique")

    def torques(self, state: RigidBodyState, inertia: np.ndarray, context: EnvironmentContext) -> TorqueBudget:
        return TorqueBudget(
            {
                name: effector.torque(state, inertia, context)
                for name, effector in self.effectors
            }
        )


def default_leo_disturbance_effectors(
    geometry: BoxGeometry | None = None,
) -> DisturbanceEffectorSet:
    """Build the default LEO disturbance response effectors."""

    box = BoxGeometry(np.array([0.10, 0.20, 0.30])) if geometry is None else geometry
    return DisturbanceEffectorSet(
        (
            ("gravity_gradient", GravityGradientTorque()),
            ("residual_magnetic", ResidualMagneticTorque()),
            ("aerodynamic", AerodynamicTorque(box)),
            ("solar_pressure", SolarPressureTorque(box)),
        )
    )


def disturbance_effectors_from_profile(
    profile: str = "default",
    geometry: BoxGeometry | None = None,
) -> DisturbanceEffectorSet:
    """Build a named lightweight disturbance subset for experiment studies."""

    box = BoxGeometry(np.array([0.10, 0.20, 0.30])) if geometry is None else geometry
    normalized = str(profile or "default")
    if normalized in {"default", "all"}:
        names = ("gravity_gradient", "residual_magnetic", "aerodynamic", "solar_pressure")
    elif normalized == "gravity_gradient_only":
        names = ("gravity_gradient",)
    elif normalized == "residual_magnetic_only":
        names = ("residual_magnetic",)
    elif normalized == "aerodynamic_only":
        names = ("aerodynamic",)
    elif normalized == "solar_pressure_only":
        names = ("solar_pressure",)
    else:
        raise ValueError(f"unsupported disturbance profile: {profile}")

    builders = {
        "gravity_gradient": lambda: GravityGradientTorque(),
        "residual_magnetic": lambda: ResidualMagneticTorque(),
        "aerodynamic": lambda: AerodynamicTorque(box),
        "solar_pressure": lambda: SolarPressureTorque(box),
    }
    return DisturbanceEffectorSet(tuple((name, builders[name]()) for name in names))
