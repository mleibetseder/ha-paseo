"""Binary sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import PaseoConfigEntry, payload
from .coordinator import PaseoCoordinator
from .entity import PaseoEntity, PaseoRunEntity, ensure_hub_device, run_unique_id

PARALLEL_UPDATES = 0  # read-only entities fed by pushes


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PaseoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            PaseoBridgeOnlineSensor(coordinator),
            PaseoNeedsAttentionSensor(coordinator),
            PaseoAgentWorkingSensor(coordinator),
        ]
    )

    live: dict[str, PaseoRunEntity] = {}  # unique id -> entity added by this setup

    @callback
    def _add_new_runs() -> None:
        """Add the working sensor of every run that has none live: new runs, and ones the user deleted."""
        hub_id: str | None = None
        entities: list[PaseoRunWorkingSensor] = []
        for key in payload.run_keys(coordinator.data):
            if run_unique_id(coordinator.instance_id, key, "project_working") in live:
                continue
            hub_id = hub_id or ensure_hub_device(hass, coordinator)
            worktree = payload.worktree(coordinator.data, key)
            entity = PaseoRunWorkingSensor(coordinator, key, worktree, hub_id)
            entity.live = live
            live[entity.unique_id] = entity
            entities.append(entity)
        if entities:
            async_add_entities(entities)

    _add_new_runs()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_runs))


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
    """On while an agent is busy or a recently updated project is working; unavailable while the data is stale."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator: PaseoCoordinator) -> None:
        """Create the sensor."""
        super().__init__(coordinator, "agent_working")

    @property
    def is_on(self) -> bool:
        """True while the payload reports a running turn, or a non-idle project works and was updated within the last hour."""
        return payload.working(self.coordinator.data)


class PaseoRunWorkingSensor(PaseoRunEntity, BinarySensorEntity):
    """On while an agent works in this run; unavailable when the run is gone or the data is stale."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(
        self, coordinator: PaseoCoordinator, run_key: str, worktree: str, hub_id: str
    ) -> None:
        """Create the sensor."""
        super().__init__(coordinator, run_key, "project_working", worktree, hub_id)

    @property
    def is_on(self) -> bool | None:
        """True while the run's working flag is set."""
        view = self.run_view
        return payload.run_working(view) if view else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The run's key and worktree."""
        return dict(self.run_attributes())
