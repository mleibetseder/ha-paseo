"""The Paseo integration: Paseo agent, quota and CI state from the hass-bridge plugin."""

from __future__ import annotations

from functools import partial
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .api import PaseoAuthError, PaseoBridgeClient, PaseoError
from .const import CONF_TOKEN, CONF_URL, CONF_WEBHOOK_ID, DOMAIN
from .coordinator import PaseoCoordinator
from .services import async_setup_services
from .webhook import (
    async_internal_url,
    async_register_webhook,
    async_unregister_webhook,
)

_LOGGER = logging.getLogger(__name__)

# The unique ids (<instance_id>_<key>) of the entities that exist; everything else in the registry is left over from
# an older version (progress, per-run, question notifier) and is removed at setup.
ENTITY_KEYS = frozenset(
    {
        "last_push",
        "ci_queue",
        "active_agents",
        "open_agents",
        "quota_waiting",
        "bridge_online",
        "needs_attention",
        "agent_working",
    }
)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

type PaseoConfigEntry = ConfigEntry[PaseoCoordinator]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services once for the domain, not per config entry."""
    async_setup_services(hass)
    return True


@callback
def _async_remove_orphans(hass: HomeAssistant, entry: ConfigEntry, instance_id: str) -> None:
    """Drop the registry entries of entities and devices that an older version created and this one does not."""
    keep = {f"{instance_id}_{key}" for key in ENTITY_KEYS}
    ent_reg = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if entity.unique_id not in keep:
            ent_reg.async_remove(entity.entity_id)
    dev_reg = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        if (DOMAIN, instance_id) not in device.identifiers:
            dev_reg.async_remove_device(device.id)


async def async_setup_entry(hass: HomeAssistant, entry: PaseoConfigEntry) -> bool:
    """Set up Paseo from a config entry."""
    client = PaseoBridgeClient(
        async_get_clientsession(hass), entry.data[CONF_URL], entry.data[CONF_TOKEN]
    )
    reachable = True
    try:
        await client.whoami()
    except PaseoAuthError as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="auth_failed"
        ) from err
    except PaseoError:
        # A plugin that is down must not block setup: the entities stay off/unavailable
        # until its pushes arrive. register() is skipped below: a second call would wait for its
        # own timeout and double the time setup takes.
        reachable = False
        _LOGGER.debug("The plugin is not reachable at setup; waiting for its pushes")

    coordinator = PaseoCoordinator(hass, entry, client)
    entry.runtime_data = coordinator
    _async_remove_orphans(hass, entry, coordinator.instance_id)
    await Store(
        hass, 1, f"paseo.{entry.entry_id}.notifier"
    ).async_remove()  # the removed notifier's state file

    webhook_id = entry.data[CONF_WEBHOOK_ID]
    async_register_webhook(hass, webhook_id, coordinator)
    entry.async_on_unload(partial(async_unregister_webhook, hass, webhook_id))
    entry.async_on_unload(coordinator.async_shutdown)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Pairing stored the URL without a push (the webhook did not exist yet). Registering the same
    # URL again (idempotent) makes the plugin push now, so bridge_online turns on at once, also
    # after every Home Assistant restart.
    if reachable:
        try:
            await client.register(async_internal_url(hass, webhook_id))
        except (PaseoError, NoURLAvailableError):
            _LOGGER.debug("Could not ask the plugin to push now; waiting for its heartbeat")
    return True


async def async_remove_entry(hass: HomeAssistant, entry: PaseoConfigEntry) -> None:
    """The entry was deleted: tell the plugin to stop pushing; never fail."""
    client = PaseoBridgeClient(
        async_get_clientsession(hass), entry.data[CONF_URL], entry.data[CONF_TOKEN]
    )
    try:
        await client.unregister()
    except PaseoError:
        _LOGGER.debug("Could not tell the plugin to unregister; it keeps pushing")
    except Exception:
        _LOGGER.debug("Unexpected error while unregistering", exc_info=True)


async def async_unload_entry(hass: HomeAssistant, entry: PaseoConfigEntry) -> bool:
    """Unload a Paseo config entry; the webhook and the timer go with it."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
