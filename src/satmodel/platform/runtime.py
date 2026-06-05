"""Lightweight runtime scheduling primitives for platform experiments."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from satmodel.platform.utils import reject_unknown


def _positive_seconds(name: str, value: float) -> float:
    number = float(value)
    if number <= 0.0:
        raise ValueError(f"{name} must be positive")
    return number


def _non_negative_seconds(name: str, value: float) -> float:
    number = float(value)
    if number < 0.0:
        raise ValueError(f"{name} must be non-negative")
    return number


@dataclass(frozen=True)
class RuntimeModule:
    """A schedulable runtime module such as a sensor, controller, actuator, or recorder."""

    name: str
    role: str = "generic"
    update_period_s: float | None = None
    priority: int = 0
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "name", str(self.name))
        object.__setattr__(self, "role", str(self.role))
        object.__setattr__(self, "priority", int(self.priority))
        object.__setattr__(self, "enabled", bool(self.enabled))
        object.__setattr__(self, "metadata", dict(self.metadata))
        if not self.name:
            raise ValueError("runtime module name must be non-empty")
        if self.update_period_s is not None:
            object.__setattr__(self, "update_period_s", _positive_seconds("runtime module update_period_s", self.update_period_s))

    def to_mapping(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "role": self.role,
            "priority": self.priority,
            "enabled": self.enabled,
            "metadata": dict(self.metadata),
        }
        if self.update_period_s is not None:
            payload["update_period_s"] = self.update_period_s
        return payload


@dataclass(frozen=True)
class RuntimeTask:
    """A task with a base cadence and ordered runtime modules."""

    name: str
    update_period_s: float
    modules: tuple[RuntimeModule, ...] = ()
    priority: int = 0
    start_s: float = 0.0
    stop_s: float | None = None
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "name", str(self.name))
        object.__setattr__(self, "update_period_s", _positive_seconds("runtime task update_period_s", self.update_period_s))
        object.__setattr__(self, "priority", int(self.priority))
        object.__setattr__(self, "start_s", _non_negative_seconds("runtime task start_s", self.start_s))
        object.__setattr__(self, "enabled", bool(self.enabled))
        object.__setattr__(self, "metadata", dict(self.metadata))
        modules = tuple(item if isinstance(item, RuntimeModule) else runtime_module_from_mapping(item) for item in self.modules)
        object.__setattr__(self, "modules", modules)
        if not self.name:
            raise ValueError("runtime task name must be non-empty")
        if self.stop_s is not None:
            stop = _positive_seconds("runtime task stop_s", self.stop_s)
            if stop <= self.start_s:
                raise ValueError("runtime task stop_s must be greater than start_s")
            object.__setattr__(self, "stop_s", stop)
        names = [module.name for module in modules]
        if len(names) != len(set(names)):
            raise ValueError(f"runtime task {self.name!r} has duplicate module names")

    def to_mapping(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "update_period_s": self.update_period_s,
            "modules": [module.to_mapping() for module in self.modules],
            "priority": self.priority,
            "start_s": self.start_s,
            "enabled": self.enabled,
            "metadata": dict(self.metadata),
        }
        if self.stop_s is not None:
            payload["stop_s"] = self.stop_s
        return payload


@dataclass(frozen=True)
class RuntimeProcess:
    """A process containing tasks that can be expanded into a deterministic schedule."""

    name: str
    tasks: tuple[RuntimeTask, ...] = ()
    priority: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "name", str(self.name))
        object.__setattr__(self, "priority", int(self.priority))
        object.__setattr__(self, "metadata", dict(self.metadata))
        tasks = tuple(item if isinstance(item, RuntimeTask) else runtime_task_from_mapping(item) for item in self.tasks)
        object.__setattr__(self, "tasks", tasks)
        if not self.name:
            raise ValueError("runtime process name must be non-empty")
        names = [task.name for task in tasks]
        if len(names) != len(set(names)):
            raise ValueError(f"runtime process {self.name!r} has duplicate task names")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tasks": [task.to_mapping() for task in self.tasks],
            "priority": self.priority,
            "metadata": dict(self.metadata),
        }

    def schedule(self, duration_s: float) -> list[dict[str, Any]]:
        """Expand enabled tasks/modules into a deterministic event list."""

        duration = _non_negative_seconds("runtime duration_s", duration_s)
        scheduled: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        for task_index, task in enumerate(self.tasks):
            if not task.enabled:
                continue
            task_stop = min(duration, duration if task.stop_s is None else task.stop_s)
            for module_index, module in enumerate(task.modules):
                if not module.enabled:
                    continue
                period = module.update_period_s or task.update_period_s
                time_s = task.start_s
                step_index = 0
                while time_s <= task_stop + 1e-12:
                    event = {
                        "time_s": round(time_s, 12),
                        "process": self.name,
                        "task": task.name,
                        "module": module.name,
                        "role": module.role,
                        "process_priority": self.priority,
                        "task_priority": task.priority,
                        "module_priority": module.priority,
                    }
                    scheduled.append(
                        (
                            (
                                event["time_s"],
                                -self.priority,
                                -task.priority,
                                task_index,
                                -module.priority,
                                module_index,
                            ),
                            event,
                        )
                    )
                    step_index += 1
                    time_s = task.start_s + step_index * period
        return [event for _key, event in sorted(scheduled, key=lambda item: item[0])]


def single_rate_runtime_process(
    dt_s: float,
    *,
    name: str = "single_rate_flight",
    task_name: str = "attitude_step",
    recorder_period_s: float | None = None,
) -> RuntimeProcess:
    """Build a single-rate process matching the current ScenarioRunner step order."""

    dt = _positive_seconds("single-rate runtime dt_s", dt_s)
    recorder_period = dt if recorder_period_s is None else _positive_seconds("single-rate recorder_period_s", recorder_period_s)
    return RuntimeProcess(
        name,
        tasks=(
            RuntimeTask(
                task_name,
                update_period_s=dt,
                modules=(
                    RuntimeModule("environment", role="environment"),
                    RuntimeModule("disturbance_model", role="disturbance"),
                    RuntimeModule("sensor_suite", role="sensor"),
                    RuntimeModule("estimator", role="estimator"),
                    RuntimeModule("controller", role="controller"),
                    RuntimeModule("actuator", role="actuator"),
                    RuntimeModule("dynamics", role="propagator"),
                    RuntimeModule("recorder", role="recorder", update_period_s=recorder_period),
                ),
                metadata={"semantic_baseline": "ScenarioRunner.step"},
            ),
        ),
        metadata={"template": "single_rate", "dt_s": dt},
    )


def runtime_module_from_mapping(value) -> RuntimeModule:
    if isinstance(value, RuntimeModule):
        return value
    data = dict(value)
    reject_unknown("runtime module", data, {"name", "role", "update_period_s", "priority", "enabled", "metadata"})
    return RuntimeModule(
        name=data["name"],
        role=data.get("role", "generic"),
        update_period_s=data.get("update_period_s"),
        priority=data.get("priority", 0),
        enabled=data.get("enabled", True),
        metadata=data.get("metadata", {}),
    )


def runtime_task_from_mapping(value) -> RuntimeTask:
    if isinstance(value, RuntimeTask):
        return value
    data = dict(value)
    reject_unknown("runtime task", data, {"name", "update_period_s", "modules", "priority", "start_s", "stop_s", "enabled", "metadata"})
    return RuntimeTask(
        name=data["name"],
        update_period_s=data["update_period_s"],
        modules=tuple(runtime_module_from_mapping(item) for item in data.get("modules", ())),
        priority=data.get("priority", 0),
        start_s=data.get("start_s", 0.0),
        stop_s=data.get("stop_s"),
        enabled=data.get("enabled", True),
        metadata=data.get("metadata", {}),
    )


def runtime_process_from_mapping(value) -> RuntimeProcess:
    if isinstance(value, RuntimeProcess):
        return value
    data = dict(value)
    if "template" in data:
        reject_unknown("runtime template", data, {"template", "dt_s", "name", "task_name", "recorder_period_s"})
        template = str(data["template"])
        if template != "single_rate":
            raise ValueError(f"unknown runtime template: {template}")
        if "dt_s" not in data:
            raise ValueError("runtime template dt_s is required")
        return single_rate_runtime_process(
            data["dt_s"],
            name=data.get("name", "single_rate_flight"),
            task_name=data.get("task_name", "attitude_step"),
            recorder_period_s=data.get("recorder_period_s"),
        )
    reject_unknown("runtime process", data, {"name", "tasks", "priority", "metadata"})
    return RuntimeProcess(
        name=data["name"],
        tasks=tuple(runtime_task_from_mapping(item) for item in data.get("tasks", ())),
        priority=data.get("priority", 0),
        metadata=data.get("metadata", {}),
    )
