"""Basic plotting helpers for examples."""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np

from satmodel.types import ReferenceAttitude, SimulationResult


def plot_result(result: SimulationResult, reference: ReferenceAttitude | None = None):
    """Plot attitude error, rates, torque, and inertia traces."""

    reference = ReferenceAttitude() if reference is None else reference
    fig, axes = plt.subplots(4, 1, figsize=(8, 9), sharex=True, constrained_layout=True)
    axes[0].plot(result.time, result.errors_to(reference))
    axes[0].set_ylabel("error deg")
    axes[1].plot(result.time, np.rad2deg(result.true_omega))
    axes[1].set_ylabel("rate deg/s")
    axes[2].plot(result.time, result.applied_torque)
    axes[2].set_ylabel("torque N m")
    if np.any(np.isfinite(result.inertia_estimate)):
        axes[3].plot(result.time, result.inertia_estimate)
    axes[3].set_ylabel("J diag")
    axes[3].set_xlabel("time s")
    for axis in axes:
        axis.grid(True, alpha=0.3)
    return fig, axes
