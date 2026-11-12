"""Diagnostics for a Paseo config entry: no secrets, no question or agent texts."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import PaseoConfigEntry, payload
from .const import CONF_NOTIFY_TARGETS, CONF_TOKEN, CONF_URL, CONF_WEBHOOK_ID

TO_REDACT = {
    CONF_TOKEN,
    CONF_WEBHOOK_ID,
    "webhook_url",
    "password",
    CONF_NOTIFY_TARGETS,
    "unique_id",
}


def _host(url: object) -> str | None:
    """scheme://host:port of the plugin URL; user info, path and query are dropped."""
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.hostname}" + (f":{parts.port}" if parts.port else "")
    except ValueError:
        return None


def _summary(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Counts of the last payload; never the texts of questions, tickets or agents."""
    if not data:
        return None
    ci = payload.ci(data)
    return {
        "schema": data.get("schema"),
        "plugin_version": data.get("version"),
        "pushed_at": data.get("pushed_at"),
        "next_push_by_s": data.get("next_push_by_s"),
        "runs": len(payload.runs(data)),
        "projects": len(payload.overview(data)),
        "agents": payload.agents(data)[0],
        "open_questions": payload.open_questions(data)[0],
        "deliverables": payload.deliverables(data)[0],
        "quota_waiting": payload.quota(data)[0],
        "needs_attention": payload.attention(data)[0],
        "working": payload.working(data),
        "ci_state": ci.get("state") if ci else None,
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PaseoConfigEntry
) -> dict[str, Any]:
    """Return the entry, the coordinator and the notifier state, redacted."""
    coordinator = entry.runtime_data
    notifier = coordinator.notifier
    return {
        "entry": async_redact_data(
            {
                "title": entry.title,
                "unique_id": entry.unique_id,
                "data": {**entry.data, CONF_URL: _host(entry.data.get(CONF_URL))},
                "options": dict(entry.options),
            },
            TO_REDACT,
        ),
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_push": coordinator.last_push.isoformat() if coordinator.last_push else None,
            "stale_timer_armed": coordinator._stale_unsub is not None,
            "last_payload": _summary(coordinator.data),
        },
        "notifier": None
        if notifier is None
        else {
            "notified": len(notifier.notified),
            "held": len(notifier.held),
            "reminded": len(notifier.reminded),
            "subscriptions": len(notifier._unsubs),
        },
    }
