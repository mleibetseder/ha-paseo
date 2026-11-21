"""Tolerant readers for the sections of the pushed payload (payload schema 2).

Every section is optional: a heartbeat carries only the envelope, and a newer or older plugin may leave
fields out. A missing or malformed section reads as empty, never as an error.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import math
from typing import Any

from homeassistant.util import dt as dt_util

MAX_ATTRIBUTE_ITEMS = 20  # defence in depth: the plugin caps its lists too


def _dicts(value: Any) -> list[dict[str, Any]]:
    """Return the dict items of a list, or an empty list for anything else."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _section(data: Mapping[str, Any] | None, key: str) -> dict[str, Any]:
    value = (data or {}).get(key)
    return value if isinstance(value, dict) else {}


def _count(value: Any, fallback: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return fallback
    return value


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def attention(data: Mapping[str, Any] | None) -> tuple[bool, list[dict[str, Any]]]:
    """Whether something needs the user, and why."""
    section = _section(data, "attention")
    return section.get("on") is True, _dicts(section.get("reasons"))[:MAX_ATTRIBUTE_ITEMS]


def agents(data: Mapping[str, Any] | None) -> tuple[int, list[dict[str, Any]]]:
    """Number of agents that are mid-turn (also those beyond the list cap) and the listed ones."""
    section = _section(data, "agents")
    items = _dicts(section.get("items"))
    return _count(section.get("count"), len(items)), items[:MAX_ATTRIBUTE_ITEMS]


def open_agents(data: Mapping[str, Any] | None) -> tuple[int, list[dict[str, Any]]] | None:
    """Number of open agents (also those beyond the list cap) and the listed ones; None when the plugin does not send agents.open (older plugin)."""
    section = _section(_section(data, "agents"), "open")
    if (
        not section
        or isinstance(section.get("count"), bool)
        or not isinstance(section.get("count"), int)
    ):
        return None
    items = _dicts(section.get("items"))
    return _count(section.get("count"), len(items)), items[:MAX_ATTRIBUTE_ITEMS]


def busy(data: Mapping[str, Any] | None) -> bool:
    """True while any agent is mid-turn; falls back to the agent count for a payload without the flag."""
    value = (data or {}).get("working")
    if isinstance(value, bool):
        return value
    return agents(data)[0] > 0


def quota(data: Mapping[str, Any] | None) -> tuple[int, datetime | None, list[dict[str, Any]]]:
    """Rate-limited agents waiting for their quota: the count (also beyond the list cap), the next due time, the jobs."""
    section = _section(data, "quota")
    items = _dicts(section.get("items"))
    value = section.get("next_due_at")
    parsed = dt_util.parse_datetime(value) if isinstance(value, str) else None
    next_due = (
        dt_util.as_local(parsed) if parsed is not None and parsed.tzinfo is not None else None
    )
    return _count(section.get("waiting"), 0), next_due, items[:MAX_ATTRIBUTE_ITEMS]


CI_STATES = ("idle", "running", "queued", "blocked")


def ci(data: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The Paseo CI plugin's queue, or None when it is not alive (stale heartbeat, plugin disabled) or the payload has no ci section.

    The sensor built on it is unavailable then. A state outside the four known values counts as not alive.
    """
    section = _section(data, "ci")
    if section.get("alive") is not True or section.get("state") not in CI_STATES:
        return None
    return section


def ci_attrs(data: Mapping[str, Any] | None) -> dict[str, Any]:
    """Gate, ETA, minutes left and the open runs of a live CI queue."""
    section = ci(data) or {}
    finish = section.get("eta_finish")
    parsed = dt_util.parse_datetime(finish) if isinstance(finish, str) else None
    gate = section.get("active_gate")
    return {
        "gate": gate if isinstance(gate, str) else None,
        "eta_finish": dt_util.as_local(parsed).isoformat()
        if parsed is not None and parsed.tzinfo is not None
        else None,
        "minutes_left": _number(section.get("minutes_left")),
        "rows": _dicts(section.get("rows"))[:MAX_ATTRIBUTE_ITEMS],
    }
