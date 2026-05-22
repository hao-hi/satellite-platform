import numpy as np

from satmodel import (
    AttitudeSensor,
    GyroSensor,
    LADRCConfig,
    LADRCController,
    LEOEnvironment,
    PDController,
    ReferenceAttitude,
    RigidBodyState,
    SensorSuite,
    TorqueActuator,
    TorqueActuatorConfig,
)
from satmodel.identification import RLSIdentifier, build_inertia_regression_matrix
from satmodel.types import EstimatedState


def test_environment_sample_and_sensor_seed_reproducibility():
    state = RigidBodyState([1.0, 0.0, 0.0, 0.0], [0.1, 0.0, 0.0])
    sample = LEOEnvironment().sample(0.0, state, np.diag([0.04, 0.08, 0.10]))
    assert sample.total_torque.shape == (3,)
    assert sample.position_eci.shape == (3,)
    sensors_a = SensorSuite(AttitudeSensor(), GyroSensor())
    sensors_b = SensorSuite(AttitudeSensor(), GyroSensor())
    sensors_a.reset(12)
    sensors_b.reset(12)
    packet_a = sensors_a.measure(state, sample, 0.0)
    packet_b = sensors_b.measure(state, sample, 0.0)
    assert np.allclose(packet_a.attitude, packet_b.attitude)
    assert np.allclose(packet_a.gyro, packet_b.gyro)


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


def test_rls_regression_and_diagnostics_are_bounded():
    matrix = build_inertia_regression_matrix([0.2, -0.1, 0.3], [0.1, 0.2, -0.1])
    identifier = RLSIdentifier()
    inertia, _ = identifier.update([0.2, -0.1, 0.3], [0.1, 0.2, -0.1], [0.01, -0.01, 0.02])
    assert matrix.shape == (3, 3)
    assert np.all(inertia > 0.0)
    assert "rls_covariance_trace" in identifier.diagnostics()
