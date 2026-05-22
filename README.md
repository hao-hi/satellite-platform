# satmodel

`satmodel` is a composable Python package for first-version satellite attitude
models, estimators, identifiers, controllers, and tuning helpers.

The package currently includes:

- quaternion rigid-body dynamics with an interface-backed RK4 stepper
- an ASTERIA-like circular-LEO engineering environment
- simplified noisy attitude and gyro sensors
- a saturated body-axis torque actuator
- PD and LADRC attitude controllers
- MEKF attitude estimation and optional diagonal RLS inertia identification
- generic grid, random, Nelder-Mead, simulated annealing, and PSO tuners

## Install

```bash
pip install -e .
```

## Quick Start

```python
from satmodel import ScenarioRunner, SimulationConfig, build_default_system

system = build_default_system(controller="pd", identify_inertia=True)
config = SimulationConfig(duration=5.0, dt=0.02)
result = ScenarioRunner(system).run(config)
print(result.metrics(config.reference))
```

The high-level API is `SatelliteSystem` plus `ScenarioRunner`. The same package
also exposes individual environment, dynamics, sensor, actuator, estimation,
identification, control, and optimization components for custom assembly.

## Examples

```bash
python examples/open_loop.py
python examples/pd_closed_loop.py
python examples/ladrc_closed_loop.py
python examples/mekf_rls_identification.py
python examples/tune_pd.py
```

Add `--plot` to the closed-loop and identification examples for basic result
plots.

## Docs

- [Architecture](docs/ARCHITECTURE.md)
- [Roadmap](docs/ROADMAP.md)
- [Reference notes](docs/REFERENCES.md)

## Tests

```bash
pytest -q
```
