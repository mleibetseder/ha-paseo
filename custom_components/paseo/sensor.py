"""Sensors."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
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
    """Add the sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [PaseoLastPushSensor(coordinator), *(PaseoSensor(coordinator, d) for d in SENSORS)]
    )


class PaseoLastPushSensor(PaseoEntity, SensorEntity):
    """When the plugin last pushed. Stays readable while the bridge is offline."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: PaseoCoordinator) -> None:
        """Create the sensor."""
        super().__init__(coordinator, "last_push")

    @property
    def available(self) -> bool:
        """Unavailable only until the first push; afterwards the last time is the answer."""
        return self.coordinator.last_push is not None

    @property
    def native_value(self) -> datetime | None:
        """Return the pushed_at of the last accepted payload."""
        return self.coordinator.last_push


@dataclass(frozen=True, kw_only=True)
class PaseoSensorDescription(SensorEntityDescription):
    """A sensor computed from the last payload."""

    value_fn: Callable[[Mapping[str, Any]], str | int | float | datetime | None]
    attrs_fn: Callable[[Mapping[str, Any]], dict[str, Any]] = lambda _: {}
    available_fn: Callable[[Mapping[str, Any]], bool] = lambda _: True


def _quota_attrs(data: Mapping[str, Any]) -> dict[str, Any]:
    _, next_due, jobs = payload.quota(data)
    return {"next_due_at": next_due.isoformat() if next_due else None, "jobs": jobs}


SENSORS: tuple[PaseoSensorDescription, ...] = (
    PaseoSensorDescription(
        key="ci_queue",
        translation_key="ci_queue",
        device_class=SensorDeviceClass.ENUM,
        options=list(payload.CI_STATES),
        value_fn=lambda data: (payload.ci(data) or {}).get("state"),
        attrs_fn=payload.ci_attrs,
        available_fn=lambda data: payload.ci(data) is not None,
    ),
    PaseoSensorDescription(
        key="active_agents",
        translation_key="active_agents",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: payload.agents(data)[0],
        attrs_fn=lambda data: {"agents": payload.agents(data)[1]},
    ),
    PaseoSensorDescription(
        key="open_agents",
        translation_key="open_agents",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: (payload.open_agents(data) or (None,))[0],
        attrs_fn=lambda data: {"agents": (payload.open_agents(data) or (0, []))[1]},
        available_fn=lambda data: payload.open_agents(data) is not None,
    ),
    PaseoSensorDescription(
        key="quota_waiting",
        translation_key="quota_waiting",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: payload.quota(data)[0],
        attrs_fn=_quota_attrs,
    ),
)


class PaseoSensor(PaseoEntity, SensorEntity):
    """A sensor computed from the last payload; unavailable until the first push and whenever the data is stale."""

    entity_description: PaseoSensorDescription
    # The lists can be long and change often; the recorder does not need them.
    _unrecorded_attributes = frozenset({"agents", "jobs", "rows"})

    def __init__(self, coordinator: PaseoCoordinator, description: PaseoSensorDescription) -> None:
        """Create the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description
        if description.key == "active_agents":
            # Renamed "Busy agents", but a fresh install keeps the id sensor.paseo_active_agents. Only a suggestion:
            # an existing registry entry, and an id the user renamed, win over it.
            self.entity_id = "sensor.paseo_active_agents"

    @property
    def available(self) -> bool:
        """Unavailable when the data is stale, and for the CI queue while the Paseo CI plugin is not alive."""
        return super().available and self.entity_description.available_fn(
            self.coordinator.data or {}
        )

    @property
    def native_value(self) -> str | int | float | datetime | None:
        """Return the value computed from the last payload."""
        return self.entity_description.value_fn(self.coordinator.data or {})

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the attributes computed from the last payload."""
        return self.entity_description.attrs_fn(self.coordinator.data or {})
