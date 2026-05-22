# satmodel Roadmap

The first package release focuses on a clean single-rate API. Follow-up work can
extend the same interfaces without reintroducing script coupling.

## Next Model Upgrades

1. Add a multi-rate scheduler for fast dynamics and slower sensors, estimation,
   and control tasks.
2. Offer a `solve_ivp` dynamics backend beside the fixed-step RK4 integrator.
3. Add environment backends for IGRF, higher-fidelity atmospheric density, and
   ephemeris-driven Sun geometry.
4. Add reaction-wheel arrays, pyramid allocation, wheel speed limits, failures,
   dead zones, and actuator lag.
5. Add richer star tracker and IMU timing/error models.

## Methods Upgrades

1. Add batch least-squares and EKF inertia-identification experiments.
2. Revisit full inertia-matrix estimation and observability diagnostics.
3. Add controller benchmarking and Monte Carlo experiment helpers.
4. Add 3D attitude visualization and wheel telemetry.
