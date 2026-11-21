"""Base entity: everything sits on one Paseo device."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

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
