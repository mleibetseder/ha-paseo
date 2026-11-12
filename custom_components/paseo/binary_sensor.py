"""Binary sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import PaseoConfigEntry, payload
from .coordinator import PaseoCoordinator
from .entity import PaseoEntity

PARALLEL_UPDATES = 0  # read-only entities fed by pushes


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PaseoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the binary sensors."""
    async_add_entities(
        [
            PaseoBridgeOnlineSensor(entry.runtime_data),
            PaseoNeedsAttentionSensor(entry.runtime_data),
            PaseoAgentWorkingSensor(entry.runtime_data),
        ]
    )


class PaseoBridgeOnlineSensor(PaseoEntity, BinarySensorEntity):
    """On while pushes keep arriving; off after 2x next_push_by_s of silence."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: PaseoCoordinator) -> None:
        """Create the sensor."""
        super().__init__(coordinator, "bridge_online")

    @property
    def available(self) -> bool:
        """Always available: silence is the 'off' state."""
        return True

    @property
    def is_on(self) -> bool:
        """True while the data is fresh."""
        return self.coordinator.last_update_success


class PaseoNeedsAttentionSensor(PaseoEntity, BinarySensorEntity):
    """On when a run is stuck, a question blocks an agent or a permission prompt waits; unavailable while the data is stale."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _unrecorded_attributes = frozenset({"reasons"})

    def __init__(self, coordinator: PaseoCoordinator) -> None:
        """Create the sensor."""
        super().__init__(coordinator, "needs_attention")

    @property
    def is_on(self) -> bool:
        """True when the payload says something needs the user."""
        return payload.attention(self.coordinator.data)[0]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Why: one entry per stuck item or blocking question."""
        return {"reasons": payload.attention(self.coordinator.data)[1]}


class PaseoAgentWorkingSensor(PaseoEntity, BinarySensorEntity):
    """On while any agent is mid-turn; unavailable while the data is stale."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator: PaseoCoordinator) -> None:
        """Create the sensor."""
        super().__init__(coordinator, "agent_working")

    @property
    def is_on(self) -> bool:
        """True while the payload reports a running turn."""
        return payload.working(self.coordinator.data)
