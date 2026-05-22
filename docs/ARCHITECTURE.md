# satmodel Architecture

`satmodel` is a composable first-version satellite attitude package.

## Public Layers

The high-level layer is:

- `SatelliteSystem`: component assembly.
- `ScenarioRunner`: single-rate scenario loop.
- `SimulationResult`: arrays and basic metrics.

The component layer is:

- `EnvironmentModel.sample(t, state, inertia)`
- `SpacecraftDynamics.step(state, torque, disturbance, dt)`
- `SensorSuite.measure(state, environment_sample, t)`
- `TorqueActuator.apply(command, dt)`
- estimator objects with `update(measurement, applied_torque, dt)`
- controller objects with `command(reference, estimate, dt)`
- optimizer objects with `optimize(objective, bounds)`

## Data Flow

Each single-rate runner step samples the environment, measures the current state,
updates the estimator from the previous applied torque, requests a controller
command, saturates the body torque, and advances rigid-body dynamics with the
environment and optional external disturbance.

The default stack is intentionally small:

- circular LEO engineering environment
- scalar-first quaternion rigid-body dynamics
- simplified attitude sensor and gyro
- saturated body-axis torque actuator
- MEKF attitude estimation
- optional diagonal RLS inertia identification
- PD or LADRC attitude control

Environment disturbance reconstruction and LADRC ESO disturbance values remain
separate diagnostics. The former is a physical residual estimate used by the
identifier; the latter is a controller-internal equivalent input torque.
