from datetime import datetime, timezone
import importlib

import numpy as np
import pytest

from satmodel import (
    AttitudeSensor,
    CenteredDipoleMagneticField,
    CircularOrbitProvider,
    CubeSatPhysicalConfig,
    EnvironmentConfig,
    EphemerisOrbitProvider,
    ExponentialAtmosphere,
    GeodeticPoint,
    GyroSensor,
    IGRFMagneticField,
    KeplerianOrbitProvider,
    LADRCConfig,
    LADRCController,
    NRLMSISAtmosphere,
    OrbitState,
    OrbitalEnvironment,
    PDController,
    ReactionWheelArrayConfig,
    ReactionWheelConfig,
    ReactionWheelStateEffector,
    ReferenceAttitude,
    RigidBodyState,
    SensorSuite,
    TorqueActuator,
    TorqueActuatorConfig,
    TLEOrbitProvider,
    build_demo_leo_environment,
    default_leo_disturbance_effectors,
)
from satmodel.identification import RLSIdentifier, build_inertia_regression_matrix
from satmodel.types import EstimatedState


def test_environment_context_disturbance_budget_and_sensor_seed_reproducibility():
    state = RigidBodyState([1.0, 0.0, 0.0, 0.0], [0.1, 0.0, 0.0])
    context = build_demo_leo_environment().sample(0.0)
    torques = default_leo_disturbance_effectors().torques(state, np.diag([0.04, 0.08, 0.10]), context)
    budget = sum(torques.terms.values(), np.zeros(3, dtype=float))
    assert context.position_eci.shape == (3,)
    assert context.sun_vector_eci.shape == (3,)
    assert context.epoch_utc.tzinfo is not None
    assert isinstance(context.geodetic, GeodeticPoint)
    assert 300e3 < context.geodetic.altitude_m < 500e3
    assert torques.total_torque.shape == (3,)
    assert np.allclose(torques.total_torque, budget)
    sensors_a = SensorSuite(AttitudeSensor(), GyroSensor())
    sensors_b = SensorSuite(AttitudeSensor(), GyroSensor())
    sensors_a.reset(12)
    sensors_b.reset(12)
    packet_a = sensors_a.measure(state, context, 0.0)
    packet_b = sensors_b.measure(state, context, 0.0)
    assert np.allclose(packet_a.attitude, packet_b.attitude)
    assert np.allclose(packet_a.gyro, packet_b.gyro)


def test_orbit_providers_and_composed_environment_keep_state_boundaries():
    epoch = datetime(2026, 1, 1, tzinfo=timezone.utc)
    circular = CircularOrbitProvider(altitude_m=400e3, inclination_deg=0.0, raan_deg=0.0, arglat0_deg=15.0)
    circular_state = circular.state_at(0.0, epoch)
    radius = np.linalg.norm(circular_state.position_eci_m)
    period = 2.0 * np.pi / circular.mean_motion_rad_s
    period_state = circular.state_at(period, epoch)
    kepler = KeplerianOrbitProvider(
        semi_major_axis_m=circular.radius_m,
        inclination_deg=0.0,
        raan_deg=0.0,
        mean_anomaly0_deg=15.0,
    )
    assert np.isclose(radius, circular.radius_m)
    assert np.linalg.norm(circular_state.velocity_eci_m_s) > 7000.0
    assert np.allclose(period_state.position_eci_m, circular_state.position_eci_m, atol=1e-5)
    assert np.allclose(kepler.state_at(0.0, epoch).position_eci_m, circular_state.position_eci_m)

    ephemeris = EphemerisOrbitProvider(
        times_s=[0.0, 10.0],
        positions_eci_m=[circular_state.position_eci_m, period_state.position_eci_m],
        velocities_eci_m_s=[circular_state.velocity_eci_m_s, period_state.velocity_eci_m_s],
    )
    interpolated = ephemeris.state_at(5.0, epoch)
    callable_provider = EphemerisOrbitProvider(lambda time_s, epoch_utc: OrbitState([time_s, 0.0, 0.0], [0.0, 1.0, 0.0]))
    environment = OrbitalEnvironment(
        EnvironmentConfig(epoch),
        callable_provider,
        CenteredDipoleMagneticField(),
        ExponentialAtmosphere(),
    )
    assert interpolated.position_eci_m.shape == (3,)
    assert environment.sample(2.0).epoch_utc == epoch.replace(second=2)


def test_field_backends_scale_and_optional_adapters_are_injectable(monkeypatch):
    epoch = datetime(2026, 1, 1, tzinfo=timezone.utc)
    dipole = CenteredDipoleMagneticField()
    near = dipole.field_eci(epoch, [6800e3, 0.0, 0.0], GeodeticPoint())
    far = dipole.field_eci(epoch, [13600e3, 0.0, 0.0], GeodeticPoint())
    atmosphere = ExponentialAtmosphere()
    assert np.linalg.norm(near) > np.linalg.norm(far)
    assert atmosphere.density_kg_m3(epoch, GeodeticPoint(0.0, 0.0, 400e3)) > atmosphere.density_kg_m3(
        epoch,
        GeodeticPoint(0.0, 0.0, 500e3),
    )

    igrf = IGRFMagneticField(model=lambda longitude, latitude, altitude, date: (100.0, 200.0, -50.0))
    msis_calls = {}

    def fake_msis(*args, **kwargs):
        msis_calls["args"] = args
        msis_calls["kwargs"] = kwargs
        return np.array([[2.5e-12, 0.0]])

    nrlmsis = NRLMSISAtmosphere(calculator=fake_msis)
    assert igrf.field_eci(epoch, [6800e3, 0.0, 0.0], GeodeticPoint(5.0, 10.0, 400e3)).shape == (3,)
    assert np.isclose(nrlmsis.density_kg_m3(epoch, GeodeticPoint(5.0, 10.0, 400e3)), 2.5e-12)
    assert msis_calls["kwargs"]["version"] == 2.1

    def missing_import(name):
        raise ImportError(name)

    monkeypatch.setattr(importlib, "import_module", missing_import)
    with pytest.raises(ImportError, match="ppigrf"):
        IGRFMagneticField()
    with pytest.raises(ImportError, match="pymsis"):
        NRLMSISAtmosphere()
    with pytest.raises(ImportError, match="sgp4"):
        TLEOrbitProvider("1 00005U 58002B", "2 00005 034.2682")


def test_actuator_and_controller_command_shapes():
    actuator = TorqueActuator(TorqueActuatorConfig(0.1))
    assert np.allclose(actuator.apply([0.3, -0.2, 0.04]), [0.1, -0.1, 0.04])
    reference = ReferenceAttitude()
    estimate = EstimatedState([0.98, 0.2, 0.0, 0.0], [0.1, 0.0, 0.0], inertia_diag=[0.04, 0.08, 0.1])
    pd = PDController()
    ladrc = LADRCController(LADRCConfig(b0=[20.0, 12.0, 10.0]))
    assert pd.command(reference, estimate, 0.02).shape == (3,)
    assert ladrc.command(reference, estimate, 0.02).shape == (3,)
    assert ladrc.disturbance_estimate_torque().shape == (3,)


def test_cubesat_mass_properties_and_reaction_wheel_allocation():
    physical = CubeSatPhysicalConfig.one_unit_reaction_wheel_demo()
    array = ReactionWheelStateEffector(ReactionWheelArrayConfig.pyramid_4wheel(max_torque_nm=1.0))
    command = np.array([0.01, -0.02, 0.015])
    assert physical.mass_properties.mass_kg > 0.0
    assert physical.geometry.projected_area([1.0, 0.0, 0.0]) > 0.0
    assert physical.mass_properties.inertia_body_kgm2.shape == (3, 3)
    assert np.allclose(array.apply(command, dt=0.0), command)
    assert array.last_telemetry.wheel_torque_nm.shape == (4,)
    assert np.allclose(array.last_telemetry.allocation_error_nm, 0.0)
    assert np.all(array.last_telemetry.wheel_momentum_capacity_nms > 0.0)


def test_custom_multiwheel_arrays_allocate_and_reject_rank_deficient_axes():
    axes = [
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
        [1.0, 1.0, 1.0],
        [1.0, -1.0, 0.0],
    ]
    wheels = tuple(ReactionWheelConfig(axis, max_torque_nm=1.0) for axis in axes)
    array = ReactionWheelStateEffector(ReactionWheelArrayConfig(wheels))
    command = np.array([0.03, -0.02, 0.01])
    assert np.allclose(array.apply(command, dt=0.01), command)
    assert array.last_telemetry.wheel_torque_nm.shape == (5,)
    assert array.last_telemetry.rank_after_failures == 3
    assert array.last_telemetry.allocation_mode == "bounded_pinv"
    with pytest.raises(ValueError, match="span body torque space"):
        ReactionWheelArrayConfig(
            tuple(ReactionWheelConfig([1.0, 0.0, 0.0]) for _ in range(3))
        )


def test_reaction_wheel_speed_limit_and_failure_telemetry():
    speed_limited = ReactionWheelStateEffector(
        ReactionWheelArrayConfig(
            (
                ReactionWheelConfig([1.0, 0.0, 0.0], 1.0, 1.0, 0.1, 0.09),
                ReactionWheelConfig([0.0, 1.0, 0.0], 1.0, 1.0, 0.1),
                ReactionWheelConfig([0.0, 0.0, 1.0], 1.0, 1.0, 0.1),
            )
        )
    )
    speed_limited.apply([-1.0, 0.0, 0.0], dt=0.1)
    assert np.isclose(speed_limited.last_telemetry.wheel_torque_nm[0], 0.1)
    assert np.isclose(speed_limited.last_telemetry.wheel_speed_rad_s[0], 0.09)
    assert speed_limited.last_telemetry.speed_saturated[0]

    array = ReactionWheelStateEffector(ReactionWheelArrayConfig.pyramid_4wheel())
    array.disable_wheel(0)
    applied = array.apply([0.001, -0.001, 0.0005], dt=0.02)
    assert applied.shape == (3,)
    assert not array.last_telemetry.enabled[0]
    assert array.last_telemetry.wheel_torque_nm.shape == (4,)
    assert array.last_telemetry.allocation_error_nm.shape == (3,)


def test_bounded_allocation_respects_torque_and_speed_windows():
    array = ReactionWheelStateEffector(
        ReactionWheelArrayConfig.orthogonal_3wheel(
            spin_inertia_kgm2=1.0,
            max_torque_nm=0.01,
            max_speed_rad_s=1.0,
        )
    )
    applied = array.apply([0.10, 0.0, 0.0], dt=0.1)
    assert np.all(array.last_telemetry.wheel_torque_nm >= array.last_telemetry.available_torque_lower_nm)
    assert np.all(array.last_telemetry.wheel_torque_nm <= array.last_telemetry.available_torque_upper_nm)
    assert np.isclose(applied[0], 0.01)
    assert np.linalg.norm(array.last_telemetry.allocation_error_nm) > 0.0

    speed_window = ReactionWheelStateEffector(
        ReactionWheelArrayConfig(
            (
                ReactionWheelConfig([1.0, 0.0, 0.0], 1.0, 1.0, 0.1, 0.09),
                ReactionWheelConfig([0.0, 1.0, 0.0], 1.0, 1.0, 0.1),
                ReactionWheelConfig([0.0, 0.0, 1.0], 1.0, 1.0, 0.1),
            )
        )
    )
    speed_window.apply([-1.0, 0.0, 0.0], dt=0.1)
    assert np.isclose(speed_window.last_telemetry.available_torque_upper_nm[0], 0.1)
    assert np.isclose(speed_window.last_telemetry.wheel_torque_nm[0], 0.1)
    speed_window.apply([-1.0, 0.0, 0.0], dt=0.0)
    assert np.isclose(speed_window.last_telemetry.available_torque_upper_nm[0], 1.0)


def test_four_wheel_failure_degrades_with_rank_aware_telemetry():
    array = ReactionWheelStateEffector(ReactionWheelArrayConfig.pyramid_4wheel(max_torque_nm=1.0))
    array.disable_wheel(0)
    command = np.array([0.01, -0.02, 0.015])
    assert np.allclose(array.apply(command, dt=0.02), command)
    assert array.last_telemetry.rank_after_failures == 3
    assert not array.last_telemetry.enabled[0]

    array.disable_wheel(1)
    degraded = array.apply([0.0, 0.01, 0.0], dt=0.02)
    assert degraded.shape == (3,)
    assert array.last_telemetry.rank_after_failures < 3
    assert np.linalg.norm(array.last_telemetry.allocation_error_nm) > 0.0


def test_nullspace_momentum_bias_preserves_body_torque_and_reduces_speed_error():
    axes = np.array(
        [
            [1.0, 1.0, 1.0],
            [1.0, -1.0, -1.0],
            [-1.0, 1.0, -1.0],
            [-1.0, -1.0, 1.0],
        ],
        dtype=float,
    )
    command = np.array([0.01, -0.005, 0.002])
    dt = 0.1

    def make_array(allocation: str, gain: float):
        return ReactionWheelStateEffector(
            ReactionWheelArrayConfig(
                tuple(
                    ReactionWheelConfig(axis, spin_inertia_kgm2=1.0e-3, max_torque_nm=10.0, max_speed_rad_s=100.0, initial_speed_rad_s=10.0)
                    for axis in axes
                ),
                allocation=allocation,
                momentum_gain=gain,
            )
        )

    baseline = make_array("bounded_pinv", 0.0)
    biased = make_array("nullspace_momentum", 1.0)
    assert np.allclose(baseline.apply(command, dt=dt), command)
    assert np.allclose(biased.apply(command, dt=dt), command)
    null_component = biased.last_telemetry.wheel_torque_nm - baseline.last_telemetry.wheel_torque_nm
    assert np.linalg.norm(biased.axis_matrix @ null_component) < 1e-12
    inertia = biased.spin_inertia
    baseline_next = baseline.state_vector() + dt * baseline.last_telemetry.wheel_torque_nm / inertia
    biased_next = biased.state_vector() + dt * biased.last_telemetry.wheel_torque_nm / inertia
    assert np.linalg.norm(biased_next) < np.linalg.norm(baseline_next)


def test_rls_regression_and_diagnostics_are_bounded():
    matrix = build_inertia_regression_matrix([0.2, -0.1, 0.3], [0.1, 0.2, -0.1])
    identifier = RLSIdentifier()
    inertia, _ = identifier.update([0.2, -0.1, 0.3], [0.1, 0.2, -0.1], [0.01, -0.01, 0.02])
    assert matrix.shape == (3, 3)
    assert np.all(inertia > 0.0)
    assert "rls_covariance_trace" in identifier.diagnostics()
