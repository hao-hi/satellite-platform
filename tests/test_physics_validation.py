"""物理守恒量和轮组耦合传播的回归测试。

这里更像“数值物理体检”：无外力矩时角动量/能量应近似守恒，反作用轮只交换系统内部角动量。
"""

import numpy as np

from satmodel import (
    ReactionWheelArrayConfig,
    ReactionWheelStateEffector,
    RigidBodyState,
    SpacecraftDynamics,
)


def _propagate(dynamics, state, torque, *, dt: float, steps: int):
    # 小工具：把同一个动力学对象按固定步长推进，便于比较不同步长下的漂移。
    states = [state.copy()]
    for _ in range(steps):
        state = dynamics.step(state, torque, dt=dt)
        states.append(state)
    return states


def test_free_rigid_body_preserves_rotational_momentum_and_energy():
    # 自由刚体无外力矩传播时，惯性系角动量和转动能量应只留下极小数值误差。
    dynamics = SpacecraftDynamics(np.diag([0.05, 0.06, 0.08]))
    initial = RigidBodyState([1.0, 0.0, 0.0, 0.0], [0.13, -0.08, 0.05])
    states = _propagate(dynamics, initial, np.zeros(3), dt=0.002, steps=800)
    momentum = np.asarray([dynamics.hub_rotational_angular_momentum(item, frame="inertial") for item in states])
    energy = np.asarray([dynamics.hub_rotational_energy(item) for item in states])
    assert np.max(np.linalg.norm(momentum - momentum[0], axis=1)) < 1e-10
    assert np.max(np.abs(energy - energy[0])) < 1e-12


def test_coupled_reaction_wheels_exchange_internal_angular_momentum():
    # 轮子加速时卫星本体获得反作用角速度，但系统总角动量仍应守恒。
    wheels = ReactionWheelStateEffector(
        ReactionWheelArrayConfig.orthogonal_3wheel(
            spin_inertia_kgm2=2.0e-4,
            max_torque_nm=1.0e-3,
            max_speed_rad_s=50.0,
        )
    )
    dynamics = SpacecraftDynamics(np.diag([0.04, 0.05, 0.06]), state_effector=wheels)
    state = RigidBodyState([1.0, 0.0, 0.0, 0.0], np.zeros(3))
    initial_momentum = dynamics.total_rotational_angular_momentum(state, frame="inertial")
    body_torque = wheels.apply([5.0e-4, 0.0, 0.0], dt=0.02)
    state = dynamics.step(state, body_torque, dt=0.02)
    total_momentum = dynamics.total_rotational_angular_momentum(state, frame="inertial")
    assert state.omega[0] > 0.0
    assert wheels.wheels[0].speed_rad_s < 0.0
    assert np.linalg.norm(total_momentum - initial_momentum) < 1e-12


def test_smaller_free_rigid_body_step_reduces_momentum_drift():
    # 固定 RK4 积分下，减小步长应降低自由刚体角动量漂移。
    inertia = np.diag([0.05, 0.06, 0.08])
    initial = RigidBodyState([1.0, 0.0, 0.0, 0.0], [0.22, -0.11, 0.07])

    def drift(dt):
        dynamics = SpacecraftDynamics(inertia)
        states = _propagate(dynamics, initial.copy(), np.zeros(3), dt=dt, steps=int(round(1.0 / dt)))
        first = dynamics.hub_rotational_angular_momentum(states[0], frame="inertial")
        last = dynamics.hub_rotational_angular_momentum(states[-1], frame="inertial")
        return float(np.linalg.norm(last - first))

    assert drift(0.005) <= drift(0.02) + 1e-14


def test_smaller_coupled_wheel_step_reduces_momentum_drift():
    # 轮组和刚体耦合传播也应呈现同样的步长收敛趋势。
    initial = RigidBodyState([1.0, 0.0, 0.0, 0.0], [0.07, -0.05, 0.03])

    def drift(dt):
        wheels = ReactionWheelStateEffector(
            ReactionWheelArrayConfig.orthogonal_3wheel(
                spin_inertia_kgm2=2.0e-4,
                max_torque_nm=1.0e-3,
                max_speed_rad_s=50.0,
            )
        )
        dynamics = SpacecraftDynamics(np.diag([0.04, 0.05, 0.06]), state_effector=wheels)
        state = initial.copy()
        first = dynamics.total_rotational_angular_momentum(state, frame="inertial")
        for _ in range(int(round(0.4 / dt))):
            body_torque = wheels.apply([4.0e-4, -2.0e-4, 1.0e-4], dt=dt)
            state = dynamics.step(state, body_torque, dt=dt)
        last = dynamics.total_rotational_angular_momentum(state, frame="inertial")
        return float(np.linalg.norm(last - first))

    assert drift(0.005) <= drift(0.02) + 1e-14
