"""Versioned, lightweight scenario specification objects."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np

from satmodel._validation import vec3
from satmodel.math import quat_normalize


def _optional_quat(value, *, name: str):
    if value is None:
        return None
    arr = np.asarray(value, dtype=float).reshape(-1)
    if arr.size != 4:
        raise ValueError(f"{name} must contain four elements")
    return quat_normalize(arr).tolist()


def _optional_vec3(value, *, name: str):
    if value is None:
        return None
    return vec3(value, name=name).tolist()


def _reject_unknown(section: str, values: dict[str, Any], allowed: set[str]):
    unknown = sorted(set(values) - allowed)
    if unknown:
        joined = ", ".join(unknown)
        raise ValueError(f"unknown {section} field(s): {joined}")


def _optional_datetime(value, *, name: str):
    if value is None:
        return None
    if isinstance(value, datetime):
        epoch = value
    else:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        epoch = datetime.fromisoformat(text)
    if epoch.tzinfo is None:
        epoch = epoch.replace(tzinfo=timezone.utc)
    return epoch.astimezone(timezone.utc).isoformat()


@dataclass
class ScenarioMetadata:
    """Human-facing scenario identifiers."""

    name: str = "scenario"
    description: str = ""
    tags: tuple[str, ...] = ()

    def __post_init__(self):
        self.name = str(self.name).strip() or "scenario"
        self.description = str(self.description)
        self.tags = tuple(str(item) for item in self.tags)


@dataclass
class ScenarioTimeSpec:
    """Single-rate timing settings consumed by the v0.2 compiler."""

    duration_s: float = 8.0
    dt_s: float = 0.02
    seed: int = 0

    def __post_init__(self):
        self.duration_s = float(self.duration_s)
        self.dt_s = float(self.dt_s)
        self.seed = int(self.seed)
        if self.duration_s <= 0.0 or self.dt_s <= 0.0:
            raise ValueError("scenario duration_s and dt_s must be positive")


@dataclass
class ScenarioSystemSpec:
    """High-level system-construction choices."""

    builder: str = "default"
    controller: str | None = "pd"
    identify_inertia: bool = False
    environment: str = "demo"
    disturbance_profile: str = "default"

    def __post_init__(self):
        self.builder = str(self.builder)
        if self.controller is not None:
            self.controller = str(self.controller)
        self.identify_inertia = bool(self.identify_inertia)
        self.environment = str(self.environment)
        self.disturbance_profile = str(self.disturbance_profile)
        if self.builder not in {"default", "ideal_torque", "cubesat_reaction_wheel"}:
            raise ValueError("system.builder must be 'default', 'ideal_torque', or 'cubesat_reaction_wheel'")
        if self.controller not in {"pd", "ladrc", "open_loop", None}:
            raise ValueError("system.controller must be 'pd', 'ladrc', 'open_loop', or null")
        if self.environment not in {"demo", "zero", "orbital"}:
            raise ValueError("system.environment must be 'demo', 'zero', or 'orbital'")
        if self.disturbance_profile not in {
            "default",
            "all",
            "gravity_gradient_only",
            "residual_magnetic_only",
            "aerodynamic_only",
            "solar_pressure_only",
        }:
            raise ValueError(
                "system.disturbance_profile must be 'default', 'all', "
                "'gravity_gradient_only', 'residual_magnetic_only', "
                "'aerodynamic_only', or 'solar_pressure_only'"
            )


@dataclass
class ScenarioControllerSpec:
    """Optional controller parameters for scenario-compiled controllers."""

    pd_kp: float | None = None
    pd_kd: float | None = None
    ladrc_omega_c: float | None = None
    ladrc_omega_o: float | None = None
    ladrc_b0: float | list[float] | None = None
    ladrc_b0_filter_alpha: float | None = None
    ladrc_adapt_b0_from_inertia: bool | None = None

    def __post_init__(self):
        if self.pd_kp is not None:
            self.pd_kp = float(self.pd_kp)
        if self.pd_kd is not None:
            self.pd_kd = float(self.pd_kd)
        if self.ladrc_omega_c is not None:
            self.ladrc_omega_c = float(self.ladrc_omega_c)
        if self.ladrc_omega_o is not None:
            self.ladrc_omega_o = float(self.ladrc_omega_o)
        if self.ladrc_b0 is not None:
            arr = np.asarray(self.ladrc_b0, dtype=float).reshape(-1)
            if arr.size == 1:
                self.ladrc_b0 = float(arr[0])
            elif arr.size == 3:
                self.ladrc_b0 = arr.tolist()
            else:
                raise ValueError("controller.ladrc_b0 must be scalar or length three")
        if self.ladrc_b0_filter_alpha is not None:
            self.ladrc_b0_filter_alpha = float(self.ladrc_b0_filter_alpha)
        if self.ladrc_adapt_b0_from_inertia is not None:
            self.ladrc_adapt_b0_from_inertia = bool(self.ladrc_adapt_b0_from_inertia)


@dataclass
class ScenarioOrbitSpec:
    """Configurable orbit provider boundary for v0.2 scenarios."""

    provider: str = "circular"
    altitude_m: float = 400e3
    inclination_deg: float = 51.6
    raan_deg: float = 25.0
    arglat0_deg: float = 40.0
    semi_major_axis_m: float | None = None
    eccentricity: float = 0.0
    arg_periapsis_deg: float = 0.0
    mean_anomaly0_deg: float = 0.0

    def __post_init__(self):
        self.provider = str(self.provider)
        if self.provider not in {"circular", "keplerian"}:
            raise ValueError("environment.orbit.provider must be 'circular' or 'keplerian'")
        self.altitude_m = float(self.altitude_m)
        self.inclination_deg = float(self.inclination_deg)
        self.raan_deg = float(self.raan_deg)
        self.arglat0_deg = float(self.arglat0_deg)
        if self.semi_major_axis_m is not None:
            self.semi_major_axis_m = float(self.semi_major_axis_m)
        self.eccentricity = float(self.eccentricity)
        self.arg_periapsis_deg = float(self.arg_periapsis_deg)
        self.mean_anomaly0_deg = float(self.mean_anomaly0_deg)


@dataclass
class ScenarioEnvironmentSpec:
    """External environment settings beyond the simple system selector."""

    epoch_utc: str | None = None
    sun_vector_eci: list[float] | None = None
    magnetic_field: str = "centered_dipole"
    atmosphere: str = "exponential"
    orbit: ScenarioOrbitSpec = field(default_factory=ScenarioOrbitSpec)

    def __post_init__(self):
        self.epoch_utc = _optional_datetime(self.epoch_utc, name="environment.epoch_utc")
        self.sun_vector_eci = _optional_vec3(self.sun_vector_eci, name="environment.sun_vector_eci")
        self.magnetic_field = str(self.magnetic_field)
        self.atmosphere = str(self.atmosphere)
        if self.magnetic_field != "centered_dipole":
            raise ValueError("environment.magnetic_field currently supports only 'centered_dipole'")
        if self.atmosphere != "exponential":
            raise ValueError("environment.atmosphere currently supports only 'exponential'")
        if not isinstance(self.orbit, ScenarioOrbitSpec):
            self.orbit = ScenarioOrbitSpec(**dict(self.orbit))


@dataclass
class ScenarioReactionWheelSpec:
    """Reaction-wheel array settings for CubeSat platform scenarios."""

    layout: str = "pyramid_4wheel"
    spin_inertia_kgm2: float = 2.6e-5
    max_torque_nm: float = 0.007
    max_speed_rad_s: float = 8000.0 * 2.0 * np.pi / 60.0
    initial_speeds_rad_s: list[float] | None = None
    allocation: str = "bounded_pinv"
    momentum_reference_rad_s: float | list[float] = 0.0
    momentum_gain: float = 0.0
    wheel_torque_weights: float | list[float] = 1.0

    def __post_init__(self):
        self.layout = str(self.layout)
        if self.layout not in {"pyramid_4wheel", "orthogonal_3wheel"}:
            raise ValueError("actuators.reaction_wheels.layout must be 'pyramid_4wheel' or 'orthogonal_3wheel'")
        self.spin_inertia_kgm2 = float(self.spin_inertia_kgm2)
        self.max_torque_nm = float(self.max_torque_nm)
        self.max_speed_rad_s = float(self.max_speed_rad_s)
        self.allocation = str(self.allocation)
        if self.allocation not in {"pinv", "bounded_pinv", "nullspace_momentum"}:
            raise ValueError("actuators.reaction_wheels.allocation must be 'pinv', 'bounded_pinv', or 'nullspace_momentum'")
        self.momentum_gain = float(self.momentum_gain)
        if self.initial_speeds_rad_s is not None:
            expected = 4 if self.layout == "pyramid_4wheel" else 3
            speeds = np.asarray(self.initial_speeds_rad_s, dtype=float).reshape(-1)
            if speeds.size != expected:
                raise ValueError(f"actuators.reaction_wheels.initial_speeds_rad_s must contain {expected} values")
            self.initial_speeds_rad_s = speeds.tolist()
        self.momentum_reference_rad_s = self._scalar_or_wheel_vector(
            self.momentum_reference_rad_s,
            name="actuators.reaction_wheels.momentum_reference_rad_s",
        )
        self.wheel_torque_weights = self._scalar_or_wheel_vector(
            self.wheel_torque_weights,
            name="actuators.reaction_wheels.wheel_torque_weights",
        )

    def _scalar_or_wheel_vector(self, value, *, name: str):
        arr = np.asarray(value, dtype=float).reshape(-1)
        expected = 4 if self.layout == "pyramid_4wheel" else 3
        if arr.size == 1:
            return float(arr[0])
        if arr.size != expected:
            raise ValueError(f"{name} must be scalar or contain {expected} values")
        return arr.tolist()


@dataclass
class ScenarioActuatorSpec:
    """Actuator settings for scenario-compiled systems."""

    reaction_wheels: ScenarioReactionWheelSpec | None = None

    def __post_init__(self):
        if self.reaction_wheels is not None and not isinstance(self.reaction_wheels, ScenarioReactionWheelSpec):
            self.reaction_wheels = ScenarioReactionWheelSpec(**dict(self.reaction_wheels))


@dataclass
class ScenarioFaultSpec:
    """Initial fault injection for lightweight platform scenarios."""

    target: str
    action: str
    index: int | None = None
    when_s: float = 0.0

    def __post_init__(self):
        self.target = str(self.target)
        self.action = str(self.action)
        self.when_s = float(self.when_s)
        if self.target != "reaction_wheel":
            raise ValueError("fault.target currently supports only 'reaction_wheel'")
        if self.action != "disable":
            raise ValueError("fault.action currently supports only 'disable'")
        if self.index is None:
            raise ValueError("reaction_wheel faults require index")
        self.index = int(self.index)
        if self.index < 0:
            raise ValueError("fault.index must be non-negative")
        if abs(self.when_s) > 1e-12:
            raise ValueError("v0.2 fault injection supports only when_s=0.0")


@dataclass
class ScenarioAttitudeSensorSpec:
    """Simplified attitude sensor settings."""

    noise_std_rad: float = 6e-4

    def __post_init__(self):
        self.noise_std_rad = float(self.noise_std_rad)
        if self.noise_std_rad < 0.0:
            raise ValueError("sensors.attitude.noise_std_rad must be non-negative")


@dataclass
class ScenarioGyroSensorSpec:
    """Simplified gyro sensor settings."""

    noise_std_rad_s: float = 1e-3
    bias_std_rad_s: float = 2e-3
    bias_rw_scale: float = 0.02

    def __post_init__(self):
        self.noise_std_rad_s = float(self.noise_std_rad_s)
        self.bias_std_rad_s = float(self.bias_std_rad_s)
        self.bias_rw_scale = float(self.bias_rw_scale)
        if self.noise_std_rad_s < 0.0 or self.bias_std_rad_s < 0.0 or self.bias_rw_scale < 0.0:
            raise ValueError("gyro noise, bias, and random-walk scale must be non-negative")


@dataclass
class ScenarioSensorSpec:
    """Sensor settings for scenario-compiled systems."""

    attitude: ScenarioAttitudeSensorSpec = field(default_factory=ScenarioAttitudeSensorSpec)
    gyro: ScenarioGyroSensorSpec = field(default_factory=ScenarioGyroSensorSpec)

    def __post_init__(self):
        if not isinstance(self.attitude, ScenarioAttitudeSensorSpec):
            self.attitude = ScenarioAttitudeSensorSpec(**dict(self.attitude))
        if not isinstance(self.gyro, ScenarioGyroSensorSpec):
            self.gyro = ScenarioGyroSensorSpec(**dict(self.gyro))


@dataclass
class ScenarioInitialStateSpec:
    """Initial rigid-body state settings."""

    use_default: bool = True
    quaternion: list[float] | None = None
    omega_rad_s: list[float] | None = None
    time_s: float = 0.0

    def __post_init__(self):
        self.use_default = bool(self.use_default)
        self.quaternion = _optional_quat(self.quaternion, name="initial_state.quaternion")
        self.omega_rad_s = _optional_vec3(self.omega_rad_s, name="initial_state.omega_rad_s")
        self.time_s = float(self.time_s)
        if not self.use_default and (self.quaternion is None or self.omega_rad_s is None):
            raise ValueError("custom initial_state requires quaternion and omega_rad_s")


@dataclass
class ScenarioReferenceSpec:
    """Reference attitude settings."""

    quaternion: list[float] | None = None
    omega_rad_s: list[float] | None = None

    def __post_init__(self):
        self.quaternion = _optional_quat(self.quaternion, name="reference.quaternion")
        self.omega_rad_s = _optional_vec3(self.omega_rad_s, name="reference.omega_rad_s")


@dataclass
class ScenarioOutputSpec:
    """Portable v0.2 result-writing settings."""

    root: str = "results/scenario"
    save_manifest_json: bool = True
    save_metrics_csv: bool = True
    save_time_history_csv: bool = True
    save_events_csv: bool = True
    save_markdown_report: bool = True

    def __post_init__(self):
        self.root = str(self.root)
        self.save_manifest_json = bool(self.save_manifest_json)
        self.save_metrics_csv = bool(self.save_metrics_csv)
        self.save_time_history_csv = bool(self.save_time_history_csv)
        self.save_events_csv = bool(self.save_events_csv)
        self.save_markdown_report = bool(self.save_markdown_report)


@dataclass
class ScenarioAcceptanceSpec:
    """Optional run-level acceptance gates on standard metrics."""

    max_final_error_deg: float | None = None
    max_rms_error_deg: float | None = None
    max_peak_torque_nm: float | None = None
    max_effort_nms: float | None = None

    def __post_init__(self):
        for name in (
            "max_final_error_deg",
            "max_rms_error_deg",
            "max_peak_torque_nm",
            "max_effort_nms",
        ):
            value = getattr(self, name)
            if value is not None:
                value = float(value)
                if value < 0.0:
                    raise ValueError(f"acceptance.{name} must be non-negative")
                setattr(self, name, value)


@dataclass
class ScenarioSpec:
    """Versioned v0.2 scenario contract."""

    schema_version: int = 1
    metadata: ScenarioMetadata = field(default_factory=ScenarioMetadata)
    time: ScenarioTimeSpec = field(default_factory=ScenarioTimeSpec)
    system: ScenarioSystemSpec = field(default_factory=ScenarioSystemSpec)
    controller: ScenarioControllerSpec = field(default_factory=ScenarioControllerSpec)
    environment: ScenarioEnvironmentSpec = field(default_factory=ScenarioEnvironmentSpec)
    actuators: ScenarioActuatorSpec = field(default_factory=ScenarioActuatorSpec)
    sensors: ScenarioSensorSpec = field(default_factory=ScenarioSensorSpec)
    faults: tuple[ScenarioFaultSpec, ...] = ()
    initial_state: ScenarioInitialStateSpec = field(default_factory=ScenarioInitialStateSpec)
    reference: ScenarioReferenceSpec = field(default_factory=ScenarioReferenceSpec)
    outputs: ScenarioOutputSpec = field(default_factory=ScenarioOutputSpec)
    acceptance: ScenarioAcceptanceSpec = field(default_factory=ScenarioAcceptanceSpec)

    def __post_init__(self):
        self.schema_version = int(self.schema_version)
        if self.schema_version != 1:
            raise ValueError("only scenario schema_version 1 is supported")
        if not isinstance(self.metadata, ScenarioMetadata):
            self.metadata = ScenarioMetadata(**dict(self.metadata))
        if not isinstance(self.time, ScenarioTimeSpec):
            self.time = ScenarioTimeSpec(**dict(self.time))
        if not isinstance(self.system, ScenarioSystemSpec):
            self.system = ScenarioSystemSpec(**dict(self.system))
        if not isinstance(self.controller, ScenarioControllerSpec):
            self.controller = ScenarioControllerSpec(**dict(self.controller))
        if not isinstance(self.environment, ScenarioEnvironmentSpec):
            self.environment = ScenarioEnvironmentSpec(**dict(self.environment))
        if not isinstance(self.actuators, ScenarioActuatorSpec):
            self.actuators = ScenarioActuatorSpec(**dict(self.actuators))
        if not isinstance(self.sensors, ScenarioSensorSpec):
            self.sensors = ScenarioSensorSpec(**dict(self.sensors))
        self.faults = tuple(item if isinstance(item, ScenarioFaultSpec) else ScenarioFaultSpec(**dict(item)) for item in self.faults)
        if not isinstance(self.initial_state, ScenarioInitialStateSpec):
            self.initial_state = ScenarioInitialStateSpec(**dict(self.initial_state))
        if not isinstance(self.reference, ScenarioReferenceSpec):
            self.reference = ScenarioReferenceSpec(**dict(self.reference))
        if not isinstance(self.outputs, ScenarioOutputSpec):
            self.outputs = ScenarioOutputSpec(**dict(self.outputs))
        if not isinstance(self.acceptance, ScenarioAcceptanceSpec):
            self.acceptance = ScenarioAcceptanceSpec(**dict(self.acceptance))


def scenario_from_mapping(values: dict[str, Any]) -> ScenarioSpec:
    """Build a strict scenario spec from a plain mapping."""

    data = dict(values)
    _reject_unknown(
        "scenario",
        data,
        {
            "schema_version",
            "metadata",
            "time",
            "system",
            "controller",
            "environment",
            "actuators",
            "sensors",
            "faults",
            "initial_state",
            "reference",
            "outputs",
            "acceptance",
        },
    )
    if "metadata" in data:
        _reject_unknown("metadata", dict(data["metadata"]), {"name", "description", "tags"})
    if "time" in data:
        _reject_unknown("time", dict(data["time"]), {"duration_s", "dt_s", "seed"})
    if "system" in data:
        _reject_unknown(
            "system",
            dict(data["system"]),
            {"builder", "controller", "identify_inertia", "environment", "disturbance_profile"},
        )
    if "controller" in data:
        _reject_unknown(
            "controller",
            dict(data["controller"]),
            {
                "pd_kp",
                "pd_kd",
                "ladrc_omega_c",
                "ladrc_omega_o",
                "ladrc_b0",
                "ladrc_b0_filter_alpha",
                "ladrc_adapt_b0_from_inertia",
            },
        )
    if "environment" in data:
        environment = dict(data["environment"])
        _reject_unknown("environment", environment, {"epoch_utc", "sun_vector_eci", "magnetic_field", "atmosphere", "orbit"})
        if "orbit" in environment:
            _reject_unknown(
                "environment.orbit",
                dict(environment["orbit"]),
                {
                    "provider",
                    "altitude_m",
                    "inclination_deg",
                    "raan_deg",
                    "arglat0_deg",
                    "semi_major_axis_m",
                    "eccentricity",
                    "arg_periapsis_deg",
                    "mean_anomaly0_deg",
                },
            )
    if "actuators" in data:
        actuators = dict(data["actuators"])
        _reject_unknown("actuators", actuators, {"reaction_wheels"})
        if "reaction_wheels" in actuators and actuators["reaction_wheels"] is not None:
            _reject_unknown(
                "actuators.reaction_wheels",
                dict(actuators["reaction_wheels"]),
                {
                    "layout",
                    "spin_inertia_kgm2",
                    "max_torque_nm",
                    "max_speed_rad_s",
                    "initial_speeds_rad_s",
                    "allocation",
                    "momentum_reference_rad_s",
                    "momentum_gain",
                    "wheel_torque_weights",
                },
            )
    if "faults" in data:
        for index, fault in enumerate(data["faults"]):
            _reject_unknown(f"faults[{index}]", dict(fault), {"target", "action", "index", "when_s"})
    if "sensors" in data:
        sensors = dict(data["sensors"])
        _reject_unknown("sensors", sensors, {"attitude", "gyro"})
        if "attitude" in sensors:
            _reject_unknown("sensors.attitude", dict(sensors["attitude"]), {"noise_std_rad"})
        if "gyro" in sensors:
            _reject_unknown("sensors.gyro", dict(sensors["gyro"]), {"noise_std_rad_s", "bias_std_rad_s", "bias_rw_scale"})
    if "initial_state" in data:
        _reject_unknown("initial_state", dict(data["initial_state"]), {"use_default", "quaternion", "omega_rad_s", "time_s"})
    if "reference" in data:
        _reject_unknown("reference", dict(data["reference"]), {"quaternion", "omega_rad_s"})
    if "outputs" in data:
        _reject_unknown(
            "outputs",
            dict(data["outputs"]),
            {
                "root",
                "save_manifest_json",
                "save_metrics_csv",
                "save_time_history_csv",
                "save_events_csv",
                "save_markdown_report",
            },
        )
    if "acceptance" in data:
        _reject_unknown(
            "acceptance",
            dict(data["acceptance"]),
            {
                "max_final_error_deg",
                "max_rms_error_deg",
                "max_peak_torque_nm",
                "max_effort_nms",
            },
        )
    return ScenarioSpec(**data)


def scenario_to_mapping(spec: ScenarioSpec) -> dict[str, Any]:
    """Return a JSON-serializable mapping for a scenario spec."""

    return asdict(spec)
