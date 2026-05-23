"""Physical configuration objects for compact satellite models."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from satmodel._validation import mat3, vec3
from satmodel.actuators import ReactionWheelArrayConfig
from satmodel.geometry import BoxGeometry


def uniform_box_inertia(mass_kg: float, size_m) -> np.ndarray:
    """Return principal inertia for a uniform box about its center of mass."""

    lx, ly, lz = vec3(size_m, name="box size")
    mass = float(mass_kg)
    if mass <= 0.0 or np.any(np.asarray([lx, ly, lz]) <= 0.0):
        raise ValueError("box mass and dimensions must be positive")
    return np.diag(
        [
            mass * (ly**2 + lz**2) / 12.0,
            mass * (lx**2 + lz**2) / 12.0,
            mass * (lx**2 + ly**2) / 12.0,
        ]
    )


@dataclass
class MassProperties:
    """Rigid-body mass, center of mass, and body-frame inertia."""

    mass_kg: float
    inertia_body_kgm2: np.ndarray
    center_of_mass_body_m: np.ndarray = field(default_factory=lambda: np.zeros(3))

    def __post_init__(self):
        self.mass_kg = float(self.mass_kg)
        self.inertia_body_kgm2 = mat3(self.inertia_body_kgm2, name="body inertia")
        self.center_of_mass_body_m = vec3(self.center_of_mass_body_m, name="center of mass")
        if self.mass_kg <= 0.0:
            raise ValueError("mass must be positive")
        if not np.allclose(self.inertia_body_kgm2, self.inertia_body_kgm2.T):
            raise ValueError("body inertia must be symmetric")
        if np.any(np.linalg.eigvalsh(self.inertia_body_kgm2) <= 0.0):
            raise ValueError("body inertia must be positive definite")


def cubesat_demo_mass_properties(
    *,
    total_mass_kg: float = 2.6,
    wheel_mass_kg: float = 0.13,
    wheel_count: int = 4,
    side_m: float = 0.1,
    wheel_offset_m: float = 0.04,
) -> MassProperties:
    """Build the 1U demo mass model used by the reaction-wheel example."""

    bus_mass = float(total_mass_kg) - int(wheel_count) * float(wheel_mass_kg)
    if bus_mass <= 0.0:
        raise ValueError("wheel masses must leave a positive bus mass")
    bus_inertia = uniform_box_inertia(bus_mass, np.full(3, float(side_m)))
    offset_inertia = int(wheel_count) * float(wheel_mass_kg) * float(wheel_offset_m) ** 2 * np.eye(3)
    return MassProperties(total_mass_kg, bus_inertia + offset_inertia)


@dataclass
class CubeSatPhysicalConfig:
    """Small rigid CubeSat plant configuration with wheel defaults."""

    geometry: BoxGeometry
    mass_properties: MassProperties
    wheel_array_config: ReactionWheelArrayConfig

    @classmethod
    def one_unit_reaction_wheel_demo(cls) -> "CubeSatPhysicalConfig":
        """Return the first package CubeSat baseline with a four-wheel pyramid."""

        size = np.full(3, 0.1, dtype=float)
        return cls(
            geometry=BoxGeometry(size),
            mass_properties=cubesat_demo_mass_properties(side_m=float(size[0])),
            wheel_array_config=ReactionWheelArrayConfig.pyramid_4wheel(),
        )
