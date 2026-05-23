"""Compact spacecraft geometry models shared by surface effectors."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from satmodel._validation import unit_vec3, vec3


@dataclass
class BoxGeometry:
    """Body-fixed rectangular bus geometry."""

    size_m: np.ndarray

    def __post_init__(self):
        self.size_m = vec3(self.size_m, name="box size")
        if np.any(self.size_m <= 0.0):
            raise ValueError("box dimensions must be positive")

    def projected_area(self, direction_body) -> float:
        """Return projected area along a body-frame direction."""

        lx, ly, lz = self.size_m
        nx, ny, nz = np.abs(unit_vec3(direction_body, allow_zero=True))
        return float(ly * lz * nx + lx * lz * ny + lx * ly * nz)
