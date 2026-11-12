"""Base entity: everything sits on one Paseo device."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import payload
from .const import DOMAIN
from .coordinator import PaseoCoordinator


class PaseoEntity(CoordinatorEntity[PaseoCoordinator]):
    """Base class of all Paseo entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PaseoCoordinator, key: str) -> None:
        """Set the unique id (<instance_id>_<key>), the translation key and the device."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.instance_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.instance_id)},
            name="Paseo",
            manufacturer="Paseo",
        )


def run_device_identifier(instance_id: str, key: str) -> str:
    """The device identifier of a run: <instance_id>_<run key>."""
    return f"{instance_id}_{key}"


def ensure_hub_device(hass: HomeAssistant, coordinator: PaseoCoordinator) -> str:
    """Register the Paseo hub device (same data as PaseoEntity) and return its id, the parent of run devices."""
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=coordinator.config_entry.entry_id,
        identifiers={(DOMAIN, coordinator.instance_id)},
        name="Paseo",
        manufacturer="Paseo",
    )
    return device.id


def run_unique_id(instance_id: str, run_key: str, key: str) -> str:
    """The unique id of a run entity: <instance_id>_run_<run key>_<key>."""
    return f"{instance_id}_run_{run_key}_{key}"


class PaseoRunEntity(CoordinatorEntity[PaseoCoordinator]):
    """Base class of the entities of one run (one device per run key)."""

    _attr_has_entity_name = True
    live: dict[str, PaseoRunEntity] | None = (
        None  # set by the platform: the unique ids it has added
    )

    async def async_will_remove_from_hass(self) -> None:
        """Forget the unique id only when the registry entry is gone (device or entity deleted).

        A renamed or disabled entity also leaves hass, but keeps its registry entry and must not be added again.
        """
        await super().async_will_remove_from_hass()
        if self.live is not None and (
            er.async_get(self.hass).async_get_entity_id(
                self.platform.domain, DOMAIN, self.unique_id
            )
            is None
        ):
            self.live.pop(self.unique_id, None)

    def __init__(
        self, coordinator: PaseoCoordinator, run_key: str, key: str, worktree: str, hub_id: str
    ) -> None:
        """Set the unique id (<instance_id>_run_<run key>_<key>) and the run's device."""
        super().__init__(coordinator)
        self.run_key = run_key
        self.worktree = worktree
        self._attr_unique_id = run_unique_id(coordinator.instance_id, run_key, key)
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, run_device_identifier(coordinator.instance_id, run_key))},
            translation_key="project",
            translation_placeholders={"worktree": worktree},
            manufacturer="Paseo",
            via_device_id=hub_id,
        )

    @property
    def run_view(self) -> dict[str, Any] | None:
        """The run in the last payload, or None once it is gone."""
        return payload.run_view(self.coordinator.data, self.run_key)

    @property
    def available(self) -> bool:
        """Unavailable when the data is stale or the run is no longer in the payload."""
        return super().available and self.run_view is not None

    def run_attributes(self) -> Mapping[str, Any]:
        """The attributes every run entity carries, so dashboards can filter."""
        return {"key": self.run_key, "worktree": self.worktree}
