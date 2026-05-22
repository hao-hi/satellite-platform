# Reference Notes

The refactor uses outside projects as design references, not as copied source.

- `brunopinto900/attitude_control_reaction_wheels` is a useful reference for
  pyramid reaction-wheel allocation, saturation concerns, and animated telemetry.
- `elharirymatteo/satellite-inertia-id` is a useful reference for separating
  simulation, actuation, sensors, excitation, and inertia-estimation experiments.
- ADRC literature and the Julia `ActiveDisturbanceRejectionControl.jl` project
  are useful context for bandwidth-parameterized LADRC and observer structure.

The first `satmodel` release keeps only the current Python project's simplified
environment, sensing, MEKF, RLS, PD, LADRC, and optimizer capabilities.
