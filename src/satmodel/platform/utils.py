"""Shared platform helpers."""

from __future__ import annotations

from typing import Any


def reject_unknown(section: str, values: dict[str, Any], allowed: set[str]):
    """Reject unknown fields in a strict platform mapping."""

    unknown = sorted(set(values) - allowed)
    if unknown:
        joined = ", ".join(unknown)
        raise ValueError(f"unknown {section} field(s): {joined}")


def set_mapping_path(mapping: dict[str, Any], path: str, value: Any):
    """Set a dotted path inside a nested scenario mapping."""

    parts = path.split(".")
    cursor = mapping
    for part in parts[:-1]:
        if not isinstance(cursor, dict) or part not in cursor:
            raise ValueError(f"unknown scenario path: {path}")
        cursor = cursor[part]
    leaf = parts[-1]
    if not isinstance(cursor, dict) or leaf not in cursor:
        raise ValueError(f"unknown scenario path: {path}")
    cursor[leaf] = value
