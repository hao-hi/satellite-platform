"""数学工具和最小刚体传播的单元测试。

这些测试不关心完整场景装配，只验证四元数工具和一步刚体积分的基本物理/数值边界。
"""

import numpy as np

from satmodel import RigidBodyState, SpacecraftDynamics
from satmodel.math import (
    quat_angle_error_deg,
    quat_error,
    quat_from_axis_angle,
    quat_mul,
    quat_normalize,
)


def test_quaternion_helpers_keep_expected_rotation():
    # 四元数工具是整个姿态链路的底层依赖：这里固定单位四元数、乘法和角误差的约定。
    identity = np.array([1.0, 0.0, 0.0, 0.0])
    quarter_turn = quat_from_axis_angle([0.0, 0.0, 1.0], np.deg2rad(90.0))
    assert np.allclose(quat_normalize(2.0 * identity), identity)
    assert np.allclose(quat_mul(identity, quarter_turn), quarter_turn)
    assert np.allclose(quat_error(identity, identity), identity)
    assert np.isclose(quat_angle_error_deg(identity, quarter_turn), 90.0)


def test_dynamics_step_preserves_quaternion_norm():
    # RK4 步进后会重新归一化四元数；同时有非零力矩时角速度应发生变化。
    dynamics = SpacecraftDynamics([0.05, 0.06, 0.07])
    state = RigidBodyState([1.0, 0.0, 0.0, 0.0], [0.1, -0.2, 0.05])
    next_state = dynamics.step(state, [0.001, 0.0, -0.001], dt=0.01)
    assert np.isclose(np.linalg.norm(next_state.quaternion), 1.0)
    assert next_state.time == 0.01
    assert not np.allclose(next_state.omega, state.omega)
