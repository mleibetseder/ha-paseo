"""Push-driven coordinator with a staleness timer."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .api import PaseoBridgeClient
from .const import ALLOWED_NEXT_PUSH_S, DOMAIN, SCHEMA_VERSION, STALE_FACTOR

if TYPE_CHECKING:
    from .notifier import QuestionNotifier

_LOGGER = logging.getLogger(__name__)


def valid_payload(payload: Any, instance_id: str | None) -> bool:
    """Check the envelope: schema 1, our instance, positive next_push_by_s, parsable pushed_at."""
    if not isinstance(payload, dict):
        return False
    schema = payload.get("schema")
    # Python treats True == 1 and 1.0 == 1; the schema says the integer 1.
    if type(schema) is not int or schema != SCHEMA_VERSION:
        return False
    if instance_id is not None and payload.get("instance_id") != instance_id:
        return False
    next_push = payload.get("next_push_by_s")
    if (
        isinstance(next_push, bool)
        or not isinstance(next_push, int | float)
        or next_push not in ALLOWED_NEXT_PUSH_S
    ):
        return False
    pushed_at = payload.get("pushed_at")
    return isinstance(pushed_at, str) and dt_util.parse_datetime(pushed_at) is not None


class PaseoCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Holds the last pushed payload; never polls."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: PaseoBridgeClient) -> None:
        """Start without data; entities are unavailable until the first push."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=None,
        )
        self.client = client
        self.instance_id: str = entry.unique_id or ""
        self.last_update_success = False
        self._stale_unsub: CALLBACK_TYPE | None = None
        self._stale_logged = False
        self.notifier: QuestionNotifier | None = None

    @property
    def last_push(self) -> datetime | None:
        """Time the plugin pushed the latest accepted payload."""
        if not self.data:
            return None
        return dt_util.parse_datetime(self.data["pushed_at"])

    @callback
    def async_handle_push(self, payload: Any) -> bool:
        """Accept a webhook payload; return False when it is ignored."""
        if not valid_payload(payload, self.instance_id):
            _LOGGER.debug("Ignoring a payload that is not a valid schema 1 envelope")
            return False
        self._cancel_stale_timer()
        if self._stale_logged:
            self._stale_logged = False
            _LOGGER.info("The Paseo bridge is pushing again")
        self.async_set_updated_data(payload)
        self._stale_unsub = async_call_later(
            self.hass, STALE_FACTOR * payload["next_push_by_s"], self._async_mark_stale
        )
        return True

    @callback
    def _async_mark_stale(self, _now: datetime) -> None:
        """No push for 2x next_push_by_s: the entities become unavailable."""
        self._stale_unsub = None
        self.last_update_success = False
        if not self._stale_logged:  # once per outage, not once per entity or per timer
            self._stale_logged = True
            _LOGGER.info(
                "The Paseo bridge has not pushed for %s s; its entities are unavailable",
                int(STALE_FACTOR * (self.data or {}).get("next_push_by_s", 0)),
            )
        self.async_update_listeners()

    @callback
    def _cancel_stale_timer(self) -> None:
        if self._stale_unsub is not None:
            self._stale_unsub()
            self._stale_unsub = None

    async def async_shutdown(self) -> None:
        """Cancel the staleness timer on unload."""
        self._cancel_stale_timer()
        await super().async_shutdown()
