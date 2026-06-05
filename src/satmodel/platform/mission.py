"""Mission sequence and mode timeline primitives for platform experiments."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from satmodel.platform.runtime import _non_negative_seconds, _positive_seconds
from satmodel.platform.utils import reject_unknown

SUPPORTED_MISSION_MODES = frozenset(
    {
        "detumble",
        "inertial_hold",
        "sun_pointing",
        "earth_pointing",
        "safe",
    }
)


@dataclass(frozen=True)
class MissionStep:
    """A named mission interval with mode and optional reference metadata."""

    name: str
    start_s: float
    stop_s: float
    mode: str
    reference: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "name", str(self.name))
        object.__setattr__(self, "mode", str(self.mode))
        object.__setattr__(self, "start_s", _non_negative_seconds("mission step start_s", self.start_s))
        object.__setattr__(self, "stop_s", _positive_seconds("mission step stop_s", self.stop_s))
        object.__setattr__(self, "reference", None if self.reference is None else str(self.reference))
        object.__setattr__(self, "metadata", dict(self.metadata))
        if not self.name:
            raise ValueError("mission step name must be non-empty")
        if not self.mode:
            raise ValueError("mission step mode must be non-empty")
        if self.mode not in SUPPORTED_MISSION_MODES:
            raise ValueError(f"unsupported mission mode: {self.mode}")
        if self.stop_s <= self.start_s:
            raise ValueError("mission step stop_s must be greater than start_s")

    def contains(self, time_s: float) -> bool:
        time = float(time_s)
        return self.start_s <= time < self.stop_s

    def to_mapping(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "start_s": self.start_s,
            "stop_s": self.stop_s,
            "mode": self.mode,
            "metadata": dict(self.metadata),
        }
        if self.reference is not None:
            payload["reference"] = self.reference
        return payload


@dataclass(frozen=True)
class ModeTimeline:
    """Queryable mode intervals derived from a mission sequence."""

    steps: tuple[MissionStep, ...] = ()

    def __post_init__(self):
        steps = tuple(item if isinstance(item, MissionStep) else mission_step_from_mapping(item) for item in self.steps)
        object.__setattr__(self, "steps", tuple(sorted(steps, key=lambda item: (item.start_s, item.stop_s, item.name))))
        _validate_no_overlaps(self.steps)

    def mode_at(self, time_s: float) -> str | None:
        step = self.step_at(time_s)
        return None if step is None else step.mode

    def step_at(self, time_s: float) -> MissionStep | None:
        for step in self.steps:
            if step.contains(time_s):
                return step
        return None

    def to_mapping(self) -> list[dict[str, Any]]:
        return [step.to_mapping() for step in self.steps]


@dataclass(frozen=True)
class MissionSequence:
    """A GMAT-style mission sequence skeleton for future runtime integration."""

    steps: tuple[MissionStep, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        steps = tuple(item if isinstance(item, MissionStep) else mission_step_from_mapping(item) for item in self.steps)
        object.__setattr__(self, "steps", tuple(sorted(steps, key=lambda item: (item.start_s, item.stop_s, item.name))))
        object.__setattr__(self, "metadata", dict(self.metadata))
        _validate_no_overlaps(self.steps)
        names = [step.name for step in self.steps]
        if len(names) != len(set(names)):
            raise ValueError("mission sequence has duplicate step names")

    @property
    def duration_s(self) -> float:
        if not self.steps:
            return 0.0
        return max(step.stop_s for step in self.steps)

    def mode_timeline(self) -> ModeTimeline:
        return ModeTimeline(self.steps)

    def active_step_at(self, time_s: float) -> MissionStep | None:
        return self.mode_timeline().step_at(time_s)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "steps": [step.to_mapping() for step in self.steps],
            "metadata": dict(self.metadata),
        }


def _validate_no_overlaps(steps: tuple[MissionStep, ...]) -> None:
    previous: MissionStep | None = None
    for step in steps:
        if previous is not None and step.start_s < previous.stop_s:
            raise ValueError(f"mission step {step.name!r} overlaps {previous.name!r}")
        previous = step


def mission_step_from_mapping(value) -> MissionStep:
    if isinstance(value, MissionStep):
        return value
    data = dict(value)
    reject_unknown("mission step", data, {"name", "start_s", "stop_s", "mode", "reference", "metadata"})
    return MissionStep(
        name=data["name"],
        start_s=data["start_s"],
        stop_s=data["stop_s"],
        mode=data["mode"],
        reference=data.get("reference"),
        metadata=data.get("metadata", {}),
    )


def single_mode_mission(
    mode: str,
    duration_s: float,
    *,
    name: str | None = None,
    reference: str | None = None,
) -> MissionSequence:
    """Build a one-step mission sequence for a normal pointing or safe mode."""

    duration = _positive_seconds("single-mode mission duration_s", duration_s)
    mode_name = str(mode)
    step_name = str(name or mode_name)
    return MissionSequence(
        steps=(
            MissionStep(
                name=step_name,
                start_s=0.0,
                stop_s=duration,
                mode=mode_name,
                reference=reference,
            ),
        ),
        metadata={"template": "single_mode", "mode": mode_name},
    )


def detumble_then_hold_mission(
    duration_s: float,
    *,
    detumble_s: float,
    hold_mode: str = "inertial_hold",
    reference: str | None = None,
) -> MissionSequence:
    """Build a common detumble-to-pointing mission sequence."""

    duration = _positive_seconds("detumble mission duration_s", duration_s)
    detumble = _positive_seconds("detumble_s", detumble_s)
    if detumble >= duration:
        raise ValueError("detumble_s must be less than duration_s")
    return MissionSequence(
        steps=(
            MissionStep("detumble", start_s=0.0, stop_s=detumble, mode="detumble"),
            MissionStep(
                hold_mode,
                start_s=detumble,
                stop_s=duration,
                mode=hold_mode,
                reference=reference,
            ),
        ),
        metadata={"template": "detumble_then_hold", "detumble_s": detumble, "hold_mode": hold_mode},
    )


def mission_sequence_from_mapping(value) -> MissionSequence:
    if isinstance(value, MissionSequence):
        return value
    data = dict(value)
    if "template" in data:
        reject_unknown(
            "mission template",
            data,
            {"template", "duration_s", "mode", "name", "reference", "detumble_s", "hold_mode"},
        )
        template = str(data["template"])
        if "duration_s" not in data:
            raise ValueError("mission template duration_s is required")
        if template == "single_mode":
            return single_mode_mission(
                data.get("mode", "inertial_hold"),
                data["duration_s"],
                name=data.get("name"),
                reference=data.get("reference"),
            )
        if template == "detumble_then_hold":
            if "detumble_s" not in data:
                raise ValueError("detumble_then_hold mission template detumble_s is required")
            return detumble_then_hold_mission(
                data["duration_s"],
                detumble_s=data["detumble_s"],
                hold_mode=data.get("hold_mode", "inertial_hold"),
                reference=data.get("reference"),
            )
        raise ValueError(f"unknown mission template: {template}")
    reject_unknown("mission sequence", data, {"steps", "metadata"})
    return MissionSequence(
        steps=tuple(mission_step_from_mapping(item) for item in data.get("steps", ())),
        metadata=data.get("metadata", {}),
    )
