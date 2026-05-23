"""Composable orbit and Earth environment models for attitude simulations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import importlib
from typing import Protocol

import numpy as np

from satmodel._validation import unit_vec3, utc_datetime, vec3
from satmodel.types import EnvironmentContext, GeodeticPoint, OrbitState


MU_EARTH_M3_S2 = 3.986004418e14
WGS84_A_M = 6378.137e3
WGS84_F = 1.0 / 298.257223563
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)
DEFAULT_DEMO_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


class EnvironmentModel(Protocol):
    """Environment contract consumed by the scenario runner."""

    def sample(self, time: float) -> EnvironmentContext:
        ...


class OrbitProvider(Protocol):
    """Source of Cartesian inertial orbit states."""

    def state_at(self, time_s: float, epoch_utc: datetime) -> OrbitState:
        ...


class MagneticFieldModel(Protocol):
    """Earth magnetic-field backend."""

    def field_eci(self, epoch_utc: datetime, position_eci_m, geodetic: GeodeticPoint) -> np.ndarray:
        ...


class AtmosphereModel(Protocol):
    """Mass-density backend."""

    def density_kg_m3(self, epoch_utc: datetime, geodetic: GeodeticPoint) -> float:
        ...


def _rot1(angle_rad: float) -> np.ndarray:
    c, s = np.cos(angle_rad), np.sin(angle_rad)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]], dtype=float)


def _rot3(angle_rad: float) -> np.ndarray:
    c, s = np.cos(angle_rad), np.sin(angle_rad)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]], dtype=float)


def _julian_date(epoch_utc: datetime) -> float:
    epoch = utc_datetime(epoch_utc)
    year, month = epoch.year, epoch.month
    day_fraction = (
        epoch.hour
        + (epoch.minute + (epoch.second + epoch.microsecond * 1.0e-6) / 60.0) / 60.0
    ) / 24.0
    day = epoch.day + day_fraction
    if month <= 2:
        year -= 1
        month += 12
    century = year // 100
    gregorian = 2 - century + century // 4
    return (
        int(365.25 * (year + 4716))
        + int(30.6001 * (month + 1))
        + day
        + gregorian
        - 1524.5
    )


def _gmst_angle_rad(epoch_utc: datetime) -> float:
    julian_date = _julian_date(epoch_utc)
    centuries = (julian_date - 2451545.0) / 36525.0
    gmst_deg = (
        280.46061837
        + 360.98564736629 * (julian_date - 2451545.0)
        + 0.000387933 * centuries**2
        - centuries**3 / 38710000.0
    )
    return float(np.deg2rad(gmst_deg % 360.0))


def _eci_to_ecef(vector_eci, epoch_utc: datetime) -> np.ndarray:
    return _rot3(_gmst_angle_rad(epoch_utc)) @ vec3(vector_eci, name="ECI vector")


def _ecef_to_eci(vector_ecef, epoch_utc: datetime) -> np.ndarray:
    return _rot3(_gmst_angle_rad(epoch_utc)).T @ vec3(vector_ecef, name="ECEF vector")


def _geodetic_from_ecef(position_ecef_m) -> GeodeticPoint:
    x, y, z = vec3(position_ecef_m, name="ECEF position")
    longitude = float(np.arctan2(y, x))
    horizontal = float(np.hypot(x, y))
    if horizontal < 1e-9:
        latitude = np.sign(z) * np.pi / 2.0
        altitude = abs(z) - WGS84_A_M * np.sqrt(1.0 - WGS84_E2)
        return GeodeticPoint(np.rad2deg(latitude), np.rad2deg(longitude), altitude)

    latitude = float(np.arctan2(z, horizontal * (1.0 - WGS84_E2)))
    altitude = 0.0
    for _ in range(6):
        sin_latitude = np.sin(latitude)
        prime_vertical = WGS84_A_M / np.sqrt(1.0 - WGS84_E2 * sin_latitude**2)
        altitude = horizontal / max(np.cos(latitude), 1e-12) - prime_vertical
        latitude = float(
            np.arctan2(
                z,
                horizontal * (1.0 - WGS84_E2 * prime_vertical / max(prime_vertical + altitude, 1.0)),
            )
        )
    longitude_deg = (np.rad2deg(longitude) + 180.0) % 360.0 - 180.0
    return GeodeticPoint(np.rad2deg(latitude), longitude_deg, altitude)


def geodetic_from_eci(position_eci_m, epoch_utc: datetime) -> GeodeticPoint:
    """Convert an inertial position into a lightweight WGS-84 geodetic point."""

    return _geodetic_from_ecef(_eci_to_ecef(position_eci_m, epoch_utc))


def _perifocal_frame(raan_deg: float, inclination_deg: float, arg_periapsis_deg: float) -> np.ndarray:
    return (
        _rot3(np.deg2rad(float(raan_deg)))
        @ _rot1(np.deg2rad(float(inclination_deg)))
        @ _rot3(np.deg2rad(float(arg_periapsis_deg)))
    )


def _solve_kepler(mean_anomaly_rad: float, eccentricity: float) -> float:
    anomaly = float((mean_anomaly_rad + np.pi) % (2.0 * np.pi) - np.pi)
    estimate = anomaly if eccentricity < 0.8 else np.sign(anomaly or 1.0) * np.pi
    for _ in range(20):
        residual = estimate - eccentricity * np.sin(estimate) - anomaly
        slope = 1.0 - eccentricity * np.cos(estimate)
        update = residual / max(abs(slope), 1e-12)
        estimate -= update
        if abs(update) < 1e-13:
            break
    return float(estimate)


@dataclass
class CircularOrbitProvider:
    """Analytic circular low-Earth orbit used by the default demo environment."""

    altitude_m: float = 400e3
    inclination_deg: float = 51.6
    raan_deg: float = 25.0
    arglat0_deg: float = 40.0
    mu_earth_m3_s2: float = MU_EARTH_M3_S2
    earth_radius_m: float = WGS84_A_M

    def __post_init__(self):
        self.altitude_m = float(self.altitude_m)
        self.mu_earth_m3_s2 = float(self.mu_earth_m3_s2)
        self.earth_radius_m = float(self.earth_radius_m)
        if self.altitude_m <= -self.earth_radius_m:
            raise ValueError("circular orbit radius must be positive")

    @property
    def radius_m(self) -> float:
        return self.earth_radius_m + self.altitude_m

    @property
    def mean_motion_rad_s(self) -> float:
        return float(np.sqrt(self.mu_earth_m3_s2 / self.radius_m**3))

    def state_at(self, time_s: float, epoch_utc: datetime) -> OrbitState:
        del epoch_utc
        argument_latitude = np.deg2rad(self.arglat0_deg) + self.mean_motion_rad_s * float(time_s)
        position_perifocal = self.radius_m * np.array(
            [np.cos(argument_latitude), np.sin(argument_latitude), 0.0],
            dtype=float,
        )
        velocity_perifocal = np.sqrt(self.mu_earth_m3_s2 / self.radius_m) * np.array(
            [-np.sin(argument_latitude), np.cos(argument_latitude), 0.0],
            dtype=float,
        )
        frame = _perifocal_frame(self.raan_deg, self.inclination_deg, 0.0)
        return OrbitState(frame @ position_perifocal, frame @ velocity_perifocal)


@dataclass
class KeplerianOrbitProvider:
    """Two-body Keplerian orbit provider from classical elements."""

    semi_major_axis_m: float
    eccentricity: float = 0.0
    inclination_deg: float = 0.0
    raan_deg: float = 0.0
    arg_periapsis_deg: float = 0.0
    mean_anomaly0_deg: float = 0.0
    mu_earth_m3_s2: float = MU_EARTH_M3_S2

    def __post_init__(self):
        self.semi_major_axis_m = float(self.semi_major_axis_m)
        self.eccentricity = float(self.eccentricity)
        self.mu_earth_m3_s2 = float(self.mu_earth_m3_s2)
        if self.semi_major_axis_m <= 0.0:
            raise ValueError("semi-major axis must be positive")
        if not 0.0 <= self.eccentricity < 1.0:
            raise ValueError("only elliptic Keplerian orbit providers are supported")

    def state_at(self, time_s: float, epoch_utc: datetime) -> OrbitState:
        del epoch_utc
        mean_motion = np.sqrt(self.mu_earth_m3_s2 / self.semi_major_axis_m**3)
        mean_anomaly = np.deg2rad(self.mean_anomaly0_deg) + mean_motion * float(time_s)
        eccentric_anomaly = _solve_kepler(mean_anomaly, self.eccentricity)
        cos_eccentric = np.cos(eccentric_anomaly)
        sin_eccentric = np.sin(eccentric_anomaly)
        radius = self.semi_major_axis_m * (1.0 - self.eccentricity * cos_eccentric)
        position_perifocal = self.semi_major_axis_m * np.array(
            [
                cos_eccentric - self.eccentricity,
                np.sqrt(1.0 - self.eccentricity**2) * sin_eccentric,
                0.0,
            ],
            dtype=float,
        )
        scale = np.sqrt(self.mu_earth_m3_s2 * self.semi_major_axis_m) / radius
        velocity_perifocal = scale * np.array(
            [
                -sin_eccentric,
                np.sqrt(1.0 - self.eccentricity**2) * cos_eccentric,
                0.0,
            ],
            dtype=float,
        )
        frame = _perifocal_frame(self.raan_deg, self.inclination_deg, self.arg_periapsis_deg)
        return OrbitState(frame @ position_perifocal, frame @ velocity_perifocal)


class EphemerisOrbitProvider:
    """Orbit provider from a callable or a linearly interpolated state table."""

    def __init__(
        self,
        source: Callable[[float, datetime], OrbitState | tuple[np.ndarray, np.ndarray]] | None = None,
        *,
        times_s=None,
        positions_eci_m=None,
        velocities_eci_m_s=None,
    ):
        self.source = source
        if source is not None:
            if any(item is not None for item in (times_s, positions_eci_m, velocities_eci_m_s)):
                raise ValueError("ephemeris callable and state table inputs are mutually exclusive")
            self.times_s = None
            return
        if any(item is None for item in (times_s, positions_eci_m, velocities_eci_m_s)):
            raise ValueError("ephemeris provider needs a callable or complete state table inputs")
        self.times_s = np.asarray(times_s, dtype=float).reshape(-1)
        self.positions_eci_m = np.asarray(positions_eci_m, dtype=float).reshape(-1, 3)
        self.velocities_eci_m_s = np.asarray(velocities_eci_m_s, dtype=float).reshape(-1, 3)
        if self.times_s.size < 2 or np.any(np.diff(self.times_s) <= 0.0):
            raise ValueError("ephemeris times must be strictly increasing")
        if self.positions_eci_m.shape[0] != self.times_s.size or self.velocities_eci_m_s.shape[0] != self.times_s.size:
            raise ValueError("ephemeris tables must share the same sample count")

    def state_at(self, time_s: float, epoch_utc: datetime) -> OrbitState:
        if self.source is not None:
            state = self.source(float(time_s), utc_datetime(epoch_utc))
            return state if isinstance(state, OrbitState) else OrbitState(*state)
        time = float(time_s)
        if time < self.times_s[0] or time > self.times_s[-1]:
            raise ValueError("ephemeris interpolation time is outside the state table")
        position = np.array([np.interp(time, self.times_s, self.positions_eci_m[:, axis]) for axis in range(3)])
        velocity = np.array([np.interp(time, self.times_s, self.velocities_eci_m_s[:, axis]) for axis in range(3)])
        return OrbitState(position, velocity)


class TLEOrbitProvider:
    """SGP4-backed TLE source treated as an ECI-like disturbance orbit input."""

    def __init__(self, line1: str, line2: str):
        try:
            api = importlib.import_module("sgp4.api")
        except ImportError as exc:
            raise ImportError("TLEOrbitProvider requires the optional 'sgp4' package") from exc
        self._jday = api.jday
        self._satellite = api.Satrec.twoline2rv(str(line1), str(line2))

    def state_at(self, time_s: float, epoch_utc: datetime) -> OrbitState:
        epoch = utc_datetime(epoch_utc) + timedelta(seconds=float(time_s))
        seconds = epoch.second + epoch.microsecond * 1.0e-6
        julian_day, fraction = self._jday(epoch.year, epoch.month, epoch.day, epoch.hour, epoch.minute, seconds)
        error_code, position_km, velocity_km_s = self._satellite.sgp4(julian_day, fraction)
        if error_code:
            raise ValueError(f"SGP4 propagation failed with error code {error_code}")
        return OrbitState(1.0e3 * np.asarray(position_km), 1.0e3 * np.asarray(velocity_km_s))


@dataclass
class CenteredDipoleMagneticField:
    """Earth-centered dipole magnetic-field approximation."""

    earth_dipole_am2: float = 7.94e22
    mu0_over_4pi: float = 1.0e-7

    def field_eci(self, epoch_utc: datetime, position_eci_m, geodetic: GeodeticPoint) -> np.ndarray:
        del epoch_utc, geodetic
        position = vec3(position_eci_m, name="ECI position")
        radius = max(float(np.linalg.norm(position)), 1e-9)
        radius_hat = position / radius
        dipole = np.array([0.0, 0.0, float(self.earth_dipole_am2)], dtype=float)
        return float(self.mu0_over_4pi) / radius**3 * (3.0 * radius_hat * (dipole @ radius_hat) - dipole)


class IGRFMagneticField:
    """Optional ppigrf-backed geomagnetic-field adapter."""

    def __init__(self, model=None):
        if model is None:
            try:
                model = importlib.import_module("ppigrf").igrf
            except ImportError as exc:
                raise ImportError("IGRFMagneticField requires the optional 'ppigrf' package") from exc
        self.model = model

    def field_eci(self, epoch_utc: datetime, position_eci_m, geodetic: GeodeticPoint) -> np.ndarray:
        del position_eci_m
        east_nt, north_nt, up_nt = self.model(
            geodetic.longitude_deg,
            geodetic.latitude_deg,
            geodetic.altitude_m / 1.0e3,
            utc_datetime(epoch_utc).replace(tzinfo=None),
        )
        latitude = np.deg2rad(geodetic.latitude_deg)
        longitude = np.deg2rad(geodetic.longitude_deg)
        east = np.array([-np.sin(longitude), np.cos(longitude), 0.0], dtype=float)
        north = np.array(
            [-np.sin(latitude) * np.cos(longitude), -np.sin(latitude) * np.sin(longitude), np.cos(latitude)],
            dtype=float,
        )
        up = np.array(
            [np.cos(latitude) * np.cos(longitude), np.cos(latitude) * np.sin(longitude), np.sin(latitude)],
            dtype=float,
        )
        field_ecef_t = 1.0e-9 * (
            east * float(np.asarray(east_nt).reshape(-1)[0])
            + north * float(np.asarray(north_nt).reshape(-1)[0])
            + up * float(np.asarray(up_nt).reshape(-1)[0])
        )
        return _ecef_to_eci(field_ecef_t, epoch_utc)


@dataclass
class ExponentialAtmosphere:
    """Engineering exponential thermosphere density approximation."""

    density_400_kg_m3: float = 4.0e-12
    scale_height_m: float = 55e3

    def __post_init__(self):
        self.density_400_kg_m3 = float(self.density_400_kg_m3)
        self.scale_height_m = float(self.scale_height_m)
        if self.density_400_kg_m3 < 0.0 or self.scale_height_m <= 0.0:
            raise ValueError("atmosphere density and scale height must be non-negative and positive")

    def density_kg_m3(self, epoch_utc: datetime, geodetic: GeodeticPoint) -> float:
        del epoch_utc
        return float(self.density_400_kg_m3 * np.exp(-(geodetic.altitude_m - 400e3) / self.scale_height_m))


@dataclass
class SpaceWeatherInputs:
    """Fixed or provided NRLMSIS solar and geomagnetic activity inputs."""

    f107: float = 150.0
    f107a: float = 150.0
    ap: float = 4.0


class NRLMSISAtmosphere:
    """Optional pymsis-backed NRLMSIS atmosphere adapter."""

    def __init__(self, *, activity: SpaceWeatherInputs | None = None, activity_provider=None, calculator=None):
        if calculator is None:
            try:
                calculator = importlib.import_module("pymsis").calculate
            except ImportError as exc:
                raise ImportError("NRLMSISAtmosphere requires the optional 'pymsis' package") from exc
        self.calculator = calculator
        self.activity = SpaceWeatherInputs() if activity is None else activity
        self.activity_provider = activity_provider

    def _activity_at(self, epoch_utc: datetime) -> SpaceWeatherInputs:
        if self.activity_provider is None:
            return self.activity
        inputs = self.activity_provider(utc_datetime(epoch_utc))
        return inputs if isinstance(inputs, SpaceWeatherInputs) else SpaceWeatherInputs(*inputs)

    def density_kg_m3(self, epoch_utc: datetime, geodetic: GeodeticPoint) -> float:
        inputs = self._activity_at(epoch_utc)
        result = self.calculator(
            np.asarray([np.datetime64(utc_datetime(epoch_utc).replace(tzinfo=None))]),
            np.asarray([geodetic.longitude_deg]),
            np.asarray([geodetic.latitude_deg]),
            np.asarray([geodetic.altitude_m / 1.0e3]),
            f107s=np.asarray([float(inputs.f107)]),
            f107as=np.asarray([float(inputs.f107a)]),
            aps=np.asarray([float(inputs.ap)]),
            version=2.1,
        )
        values = np.asarray(result, dtype=float)
        return float(values.reshape(-1, values.shape[-1])[0, 0])


@dataclass
class EnvironmentConfig:
    """Scenario-owned external-field configuration."""

    epoch_utc: datetime
    sun_vector_eci: np.ndarray = field(default_factory=lambda: np.array([1.0, 0.25, 0.10]))
    earth_radius_m: float = WGS84_A_M

    def __post_init__(self):
        self.epoch_utc = utc_datetime(self.epoch_utc)
        self.sun_vector_eci = unit_vec3(self.sun_vector_eci, allow_zero=True)
        self.earth_radius_m = float(self.earth_radius_m)
        if self.earth_radius_m <= 0.0:
            raise ValueError("Earth radius must be positive")


class OrbitalEnvironment:
    """Environment assembled from orbit, magnetic, and atmosphere backends."""

    def __init__(
        self,
        config: EnvironmentConfig,
        orbit_provider: OrbitProvider,
        magnetic_field_model: MagneticFieldModel,
        atmosphere_model: AtmosphereModel,
    ):
        self.config = config
        self.orbit_provider = orbit_provider
        self.magnetic_field_model = magnetic_field_model
        self.atmosphere_model = atmosphere_model

    def eclipsed(self, position_eci_m) -> bool:
        position = vec3(position_eci_m, name="ECI position")
        sun_hat = self.config.sun_vector_eci
        if float(position @ sun_hat) >= 0.0:
            return False
        radial = position - (position @ sun_hat) * sun_hat
        return bool(np.linalg.norm(radial) <= self.config.earth_radius_m)

    def sample(self, time: float) -> EnvironmentContext:
        elapsed = float(time)
        epoch = self.config.epoch_utc + timedelta(seconds=elapsed)
        orbit_state = self.orbit_provider.state_at(elapsed, self.config.epoch_utc)
        geodetic = geodetic_from_eci(orbit_state.position_eci_m, epoch)
        field_eci = self.magnetic_field_model.field_eci(epoch, orbit_state.position_eci_m, geodetic)
        density = self.atmosphere_model.density_kg_m3(epoch, geodetic)
        return EnvironmentContext(
            position_eci=orbit_state.position_eci_m,
            velocity_eci=orbit_state.velocity_eci_m_s,
            magnetic_field_eci=field_eci,
            sun_vector_eci=self.config.sun_vector_eci,
            density=density,
            eclipse=self.eclipsed(orbit_state.position_eci_m),
            epoch_utc=epoch,
            geodetic=geodetic,
        )


def build_demo_leo_environment(epoch_utc: datetime | None = None) -> OrbitalEnvironment:
    """Return the lightweight circular-LEO environment used by package examples."""

    config = EnvironmentConfig(DEFAULT_DEMO_EPOCH if epoch_utc is None else epoch_utc)
    return OrbitalEnvironment(
        config,
        CircularOrbitProvider(earth_radius_m=config.earth_radius_m),
        CenteredDipoleMagneticField(),
        ExponentialAtmosphere(),
    )


class ZeroEnvironment:
    """Environment useful for open-loop and unit-test scenarios."""

    def __init__(self, epoch_utc: datetime | None = None):
        self.epoch_utc = DEFAULT_DEMO_EPOCH if epoch_utc is None else utc_datetime(epoch_utc)

    def sample(self, time: float) -> EnvironmentContext:
        epoch = self.epoch_utc + timedelta(seconds=float(time))
        return EnvironmentContext(epoch_utc=epoch)
