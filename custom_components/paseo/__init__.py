"""The Paseo integration: Paseo agent, progress and question state from the hass-bridge plugin."""

from __future__ import annotations

from functools import partial
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from . import payload
from .actions import async_listen_for_actions
from .api import PaseoAuthError, PaseoBridgeClient, PaseoError
from .const import CONF_TOKEN, CONF_URL, CONF_WEBHOOK_ID, DOMAIN
from .coordinator import PaseoCoordinator
from .entity import run_device_identifier
from .notifier import STORE_VERSION, QuestionNotifier
from .services import async_setup_services
from .webhook import (
    async_internal_url,
    async_register_webhook,
    async_unregister_webhook,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

type PaseoConfigEntry = ConfigEntry[PaseoCoordinator]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services once for the domain, not per config entry."""
    async_setup_services(hass)
    return True


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
    notifier = QuestionNotifier(hass, entry, coordinator)
    coordinator.notifier = notifier
    await notifier.async_start()
    entry.async_on_unload(notifier.async_stop)

    webhook_id = entry.data[CONF_WEBHOOK_ID]
    async_register_webhook(hass, webhook_id, coordinator)
    entry.async_on_unload(partial(async_unregister_webhook, hass, webhook_id))
    entry.async_on_unload(coordinator.async_shutdown)
    entry.async_on_unload(async_listen_for_actions(hass, coordinator))

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
    """The entry was deleted: drop the notifier state and tell the plugin to stop pushing; never fail."""
    await Store(hass, STORE_VERSION, f"paseo.{entry.entry_id}.notifier").async_remove()
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


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: PaseoConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Allow deleting a run's device once its key is no longer in the payload; never the hub."""
    coordinator = getattr(entry, "runtime_data", None)
    live = {
        run_device_identifier(entry.unique_id or "", key)
        for key in payload.run_keys(coordinator.data if coordinator else None)
    }
    prefix = f"{entry.unique_id or ''}_"
    return any(
        domain == DOMAIN and identifier.startswith(prefix) and identifier not in live
        for domain, identifier in device_entry.identifiers
    )
