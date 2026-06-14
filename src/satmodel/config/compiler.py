"""Compile lightweight scenario specs into existing satmodel runtime objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

from satmodel.config.schema import ScenarioSpec
from satmodel.actuators import ReactionWheelArrayConfig, ReactionWheelConfig
from satmodel.controllers import LADRCConfig, LADRCController, PDController
from satmodel.disturbances import disturbance_effectors_from_profile
from satmodel.environment import (
    CenteredDipoleMagneticField,
    CircularOrbitProvider,
    EnvironmentConfig,
    ExponentialAtmosphere,
    KeplerianOrbitProvider,
    OrbitalEnvironment,
    ZeroEnvironment,
)
from satmodel.physics import CubeSatPhysicalConfig
from satmodel.sensors import AttitudeSensor, GyroSensor, SensorSuite
from satmodel.system import build_cubesat_reaction_wheel_system, build_default_system
from satmodel.types import ReferenceAttitude, RigidBodyState, SimulationConfig


@dataclass(frozen=True)
class CompiledScenario:
    """Runtime objects produced from a scenario spec."""

    spec: ScenarioSpec
    system: object
    config: SimulationConfig


def _environment_from_spec(spec: ScenarioSpec):
    if spec.system.environment == "zero":
        epoch = None if spec.environment.epoch_utc is None else datetime.fromisoformat(spec.environment.epoch_utc)
        return ZeroEnvironment(epoch)
    if spec.system.environment == "demo":
        return None
    if spec.system.environment == "orbital":
        epoch = None if spec.environment.epoch_utc is None else datetime.fromisoformat(spec.environment.epoch_utc)
        config = EnvironmentConfig(
            epoch_utc=datetime(2026, 1, 1, tzinfo=timezone.utc) if epoch is None else epoch,
            sun_vector_eci=np.array([1.0, 0.25, 0.10], dtype=float)
            if spec.environment.sun_vector_eci is None
            else np.asarray(spec.environment.sun_vector_eci, dtype=float),
        )
        orbit_spec = spec.environment.orbit
        if orbit_spec.provider == "circular":
            orbit = CircularOrbitProvider(
                altitude_m=orbit_spec.altitude_m,
                inclination_deg=orbit_spec.inclination_deg,
                raan_deg=orbit_spec.raan_deg,
                arglat0_deg=orbit_spec.arglat0_deg,
                earth_radius_m=config.earth_radius_m,
            )
        elif orbit_spec.provider == "keplerian":
            if orbit_spec.semi_major_axis_m is None:
                raise ValueError("keplerian scenarios require environment.orbit.semi_major_axis_m")
            orbit = KeplerianOrbitProvider(
                semi_major_axis_m=orbit_spec.semi_major_axis_m,
                eccentricity=orbit_spec.eccentricity,
                inclination_deg=orbit_spec.inclination_deg,
                raan_deg=orbit_spec.raan_deg,
                arg_periapsis_deg=orbit_spec.arg_periapsis_deg,
                mean_anomaly0_deg=orbit_spec.mean_anomaly0_deg,
            )
        else:
            raise ValueError(f"unsupported orbit provider: {orbit_spec.provider}")
        return OrbitalEnvironment(config, orbit, CenteredDipoleMagneticField(), ExponentialAtmosphere())
    raise ValueError(f"unsupported environment selector: {spec.system.environment}")


def _controller_from_spec(spec: ScenarioSpec):
    controller = spec.system.controller
    if controller in {None, "open_loop"}:
        return None
    params = spec.controller
    if controller == "pd":
        kwargs = {}
        if params.pd_kp is not None:
            kwargs["kp"] = params.pd_kp
        if params.pd_kd is not None:
            kwargs["kd"] = params.pd_kd
        return PDController(**kwargs)
    if controller == "ladrc":
        kwargs = {}
        if params.ladrc_omega_c is not None:
            kwargs["omega_c"] = params.ladrc_omega_c
        if params.ladrc_omega_o is not None:
            kwargs["omega_o"] = params.ladrc_omega_o
        if params.ladrc_b0 is not None:
            kwargs["b0"] = params.ladrc_b0
        if params.ladrc_b0_filter_alpha is not None:
            kwargs["b0_filter_alpha"] = params.ladrc_b0_filter_alpha
        if params.ladrc_adapt_b0_from_inertia is not None:
            kwargs["adapt_b0_from_inertia"] = params.ladrc_adapt_b0_from_inertia
        return LADRCController(LADRCConfig(**kwargs))
    raise ValueError(f"unsupported controller: {controller}")


def _wheel_array_from_spec(spec: ScenarioSpec):
    wheel_spec = spec.actuators.reaction_wheels
    if wheel_spec is None:
        return None
    speeds = (
        np.zeros(4 if wheel_spec.layout == "pyramid_4wheel" else 3, dtype=float)
        if wheel_spec.initial_speeds_rad_s is None
        else np.asarray(wheel_spec.initial_speeds_rad_s, dtype=float)
    )
    common = {
        "spin_inertia_kgm2": wheel_spec.spin_inertia_kgm2,
        "max_torque_nm": wheel_spec.max_torque_nm,
        "max_speed_rad_s": wheel_spec.max_speed_rad_s,
        "allocation": wheel_spec.allocation,
        "momentum_reference_rad_s": wheel_spec.momentum_reference_rad_s,
        "momentum_gain": wheel_spec.momentum_gain,
        "wheel_torque_weights": wheel_spec.wheel_torque_weights,
    }
    if wheel_spec.layout == "orthogonal_3wheel":
        axes = np.eye(3)
    elif wheel_spec.layout == "pyramid_4wheel":
        axes = np.array(
            [
                [1.0, 1.0, 1.0],
                [1.0, -1.0, -1.0],
                [-1.0, 1.0, -1.0],
                [-1.0, -1.0, 1.0],
            ],
            dtype=float,
        )
    else:
        raise ValueError(f"unsupported reaction-wheel layout: {wheel_spec.layout}")
    wheels = tuple(
        ReactionWheelConfig(
            axis,
            spin_inertia_kgm2=wheel_spec.spin_inertia_kgm2,
            max_torque_nm=wheel_spec.max_torque_nm,
            max_speed_rad_s=wheel_spec.max_speed_rad_s,
            initial_speed_rad_s=float(speed),
        )
        for axis, speed in zip(axes, speeds)
    )
    return ReactionWheelArrayConfig(wheels, **{key: value for key, value in common.items() if key not in {
        "spin_inertia_kgm2",
        "max_torque_nm",
        "max_speed_rad_s",
    }})


def _physical_config_from_spec(spec: ScenarioSpec):
    wheel_array = _wheel_array_from_spec(spec)
    if wheel_array is None:
        return None
    base = CubeSatPhysicalConfig.one_unit_reaction_wheel_demo()
    return CubeSatPhysicalConfig(base.geometry, base.mass_properties, wheel_array)


def _apply_initial_faults(system, spec: ScenarioSpec):
    for fault in spec.faults:
        if fault.target == "reaction_wheel" and fault.action == "disable":
            if not hasattr(system.actuator, "disable_wheel"):
                raise ValueError("reaction_wheel faults require a reaction-wheel actuator")
            system.actuator.disable_wheel(fault.index)


def _sensors_from_spec(spec: ScenarioSpec) -> SensorSuite:
    return SensorSuite(
        attitude=AttitudeSensor(noise_std_rad=spec.sensors.attitude.noise_std_rad),
        gyro=GyroSensor(
            noise_std_rad_s=spec.sensors.gyro.noise_std_rad_s,
            bias_std_rad_s=spec.sensors.gyro.bias_std_rad_s,
            bias_rw_scale=spec.sensors.gyro.bias_rw_scale,
        ),
    )


def _apply_sensors(system, spec: ScenarioSpec):
    system.sensors = _sensors_from_spec(spec)


def _system_from_spec(spec: ScenarioSpec):
    environment = _environment_from_spec(spec)
    disturbances = None
    if spec.system.disturbance_profile != "default":
        geometry = None
        if spec.system.builder == "cubesat_reaction_wheel":
            physical_config = _physical_config_from_spec(spec)
            geometry = None if physical_config is None else physical_config.geometry
        disturbances = disturbance_effectors_from_profile(spec.system.disturbance_profile, geometry=geometry)
    kwargs = {
        "controller": _controller_from_spec(spec),
        "identify_inertia": spec.system.identify_inertia,
    }
    if environment is not None:
        kwargs["environment"] = environment
    if disturbances is not None:
        kwargs["disturbances"] = disturbances
    if spec.system.builder in {"default", "ideal_torque"}:
        system = build_default_system(**kwargs)
        _apply_sensors(system, spec)
        _apply_initial_faults(system, spec)
        return system
    if spec.system.builder == "cubesat_reaction_wheel":
        physical_config = _physical_config_from_spec(spec)
        if physical_config is not None:
            kwargs["physical_config"] = physical_config
        system = build_cubesat_reaction_wheel_system(**kwargs)
        _apply_sensors(system, spec)
        _apply_initial_faults(system, spec)
        return system
    raise ValueError(f"unsupported system builder: {spec.system.builder}")


def _initial_state_from_spec(spec: ScenarioSpec):
    item = spec.initial_state
    if item.use_default:
        return None
    return RigidBodyState(
        quaternion=np.asarray(item.quaternion, dtype=float),
        omega=np.asarray(item.omega_rad_s, dtype=float),
        time=item.time_s,
    )


def _reference_from_spec(spec: ScenarioSpec) -> ReferenceAttitude:
    item = spec.reference
    return ReferenceAttitude(
        quaternion=np.array([1.0, 0.0, 0.0, 0.0], dtype=float) if item.quaternion is None else np.asarray(item.quaternion),
        omega=np.zeros(3, dtype=float) if item.omega_rad_s is None else np.asarray(item.omega_rad_s),
    )


def compile_scenario(spec: ScenarioSpec) -> CompiledScenario:
    """Compile a scenario spec into a system and simulation config."""

    system = _system_from_spec(spec)
    config = SimulationConfig(
        duration=spec.time.duration_s,
        dt=spec.time.dt_s,
        seed=spec.time.seed,
        reference=_reference_from_spec(spec),
        initial_state=_initial_state_from_spec(spec),
    )
    return CompiledScenario(spec=spec, system=system, config=config)
