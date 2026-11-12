"""Tolerant readers for the progress sections of the pushed payload (payload schema).

Every section is optional: a heartbeat carries only the envelope, and a newer or older plugin may leave
fields out. A missing or malformed section reads as empty, never as an error.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import hashlib
import json
import math
from typing import Any

from homeassistant.util import dt as dt_util

MAX_STATE_LENGTH = 255  # Home Assistant limits a state to 255 characters
MAX_ATTRIBUTE_ITEMS = 20  # defence in depth: the plugin caps its lists too
MAX_OVERVIEW_ITEMS = 10


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


def ticker(data: Mapping[str, Any] | None) -> str | None:
    """The 'what I am doing now' line, cut to a state's length."""
    value = (data or {}).get("ticker")
    if not isinstance(value, str) or not value.strip():
        return None
    return value[:MAX_STATE_LENGTH]


def runs(data: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The open runs."""
    return _dicts((data or {}).get("runs"))


def active_ticket(data: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The working ticket of the first (most recently updated) run that has one, with its worktree."""
    for run in runs(data):
        if run.get("idle") is True:
            continue
        ticket = run.get("active_ticket")
        if isinstance(ticket, dict) and ticket.get("id"):
            return {**ticket, "worktree": run.get("worktree")}
    return None


def eta(data: Mapping[str, Any] | None) -> dict[str, Any]:
    """The ETA of the most recently updated non-idle run (empty when there is none)."""
    return _section(data, "eta")


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def eta_number(data: Mapping[str, Any] | None, key: str) -> int | float | None:
    """A numeric ETA field, or None when missing or malformed."""
    return _number(eta(data).get(key))


def finish_at(data: Mapping[str, Any] | None) -> datetime | None:
    """The calibrated finish time as an aware datetime in Home Assistant's time zone."""
    value = eta(data).get("finish_at")
    parsed = dt_util.parse_datetime(value) if isinstance(value, str) else None
    if parsed is None or parsed.tzinfo is None:
        return None
    return dt_util.as_local(parsed)


def deliverables(data: Mapping[str, Any] | None) -> tuple[int, list[dict[str, Any]]]:
    """Total deliverable count over all runs and the newest items (each tagged with its worktree)."""
    total = 0
    items: list[dict[str, Any]] = []
    for run in runs(data):
        section = _section(run, "deliverables")
        listed = _dicts(section.get("items"))
        total += _count(section.get("count"), len(listed))
        items.extend({**item, "worktree": run.get("worktree")} for item in listed)
    return total, items[:MAX_ATTRIBUTE_ITEMS]


def open_questions(data: Mapping[str, Any] | None) -> tuple[int, list[dict[str, Any]]]:
    """Number of open questions (also those beyond the list cap) and the listed ones."""
    section = _section(data, "questions")
    items = _dicts(section.get("items"))
    return _count(section.get("count"), len(items)), items[:MAX_ATTRIBUTE_ITEMS]


def is_idle(project: dict[str, Any]) -> bool:
    """An open run with no update for 7 days."""
    return project.get("idle") is True


def overview(data: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """One entry per open run."""
    return _dicts((data or {}).get("overview"))[:MAX_OVERVIEW_ITEMS]


def attention(data: Mapping[str, Any] | None) -> tuple[bool, list[dict[str, Any]]]:
    """Whether something needs the user, and why."""
    section = _section(data, "attention")
    return section.get("on") is True, _dicts(section.get("reasons"))[:MAX_ATTRIBUTE_ITEMS]


def agents(data: Mapping[str, Any] | None) -> tuple[int, list[dict[str, Any]]]:
    """Number of agents that are mid-turn (also those beyond the list cap) and the listed ones."""
    section = _section(data, "agents")
    items = _dicts(section.get("items"))
    return _count(section.get("count"), len(items)), items[:MAX_ATTRIBUTE_ITEMS]


def working(data: Mapping[str, Any] | None) -> bool:
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


def _well_formed(value: Any) -> Any:
    """A lone surrogate becomes U+FFFD, like String.prototype.toWellFormed in the plugin."""
    if not isinstance(value, str):
        return value
    return value.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def well_formed_deep(value: Any) -> Any:
    """`value` with a lone surrogate in any string, key or nested item turned into U+FFFD.

    A snapshot from an older plugin could still hold one; it would make the Store save fail.
    """
    if isinstance(value, str):
        return _well_formed(value)
    if isinstance(value, dict):
        return {_well_formed(k): well_formed_deep(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [well_formed_deep(v) for v in value]
    return value


def fingerprint(question: Mapping[str, Any]) -> str:
    """A short hash of what the user decides on: title, text, mode and the options.

    A new run reuses ids from Q1 again; the fingerprint tells a different question from the one notified.
    The plugin computes the same value (REST API contract, "Question fingerprint").
    """
    options = [
        [_well_formed(o.get("letter")), _well_formed(o.get("label"))]
        for o in question.get("options") or []
        if isinstance(o, dict)
    ]
    raw = json.dumps(
        [
            _well_formed(question.get("title")),
            _well_formed(question.get("question")),
            question.get("mode"),
            options,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha1(raw.encode()).hexdigest()[:8]
