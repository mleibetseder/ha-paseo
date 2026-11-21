"""Services of the Paseo integration; they call the plugin's REST API."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from . import payload
from .api import (
    PaseoAuthError,
    PaseoDaemonError,
    PaseoError,
    PaseoNoMatchError,
    PaseoRejectedError,
    PaseoServerError,
)
from .const import (
    ATTR_AGENT_ID,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_RUN_ID,
    DOMAIN,
    SERVICE_CANCEL_CI_RUN,
    SERVICE_RESUME_QUOTA_AGENT,
)
from .coordinator import PaseoCoordinator

# The same rule as the plugin: 1 to 128 characters, no control characters.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _agent_id(value: Any) -> str:
    text = cv.string(value)
    if not 1 <= len(text) <= 128 or _CONTROL.search(text):
        raise vol.Invalid("agent_id must be 1 to 128 characters without control characters")
    return text


# The CI plugin's run id charset.
_RUN_ID = re.compile(r"[A-Za-z0-9-]{1,64}\Z")

CANCEL_CI_RUN_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_RUN_ID): vol.All(cv.string, vol.Match(_RUN_ID)),
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)

RESUME_QUOTA_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_AGENT_ID, default="all"): _agent_id,
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)


def _coordinator(hass: HomeAssistant, entry_id: str | None) -> PaseoCoordinator:
    """The loaded entry the call is for: the given one, or the only one."""
    if entry_id is not None:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entry_not_found",
                translation_placeholders={"entry_id": entry_id},
            )
        entries = [entry]
    else:
        entries = hass.config_entries.async_entries(DOMAIN)
    loaded = [entry for entry in entries if entry.state is ConfigEntryState.LOADED]
    if not loaded:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="entry_not_loaded")
    if len(loaded) > 1:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="entry_required")
    return loaded[0].runtime_data


async def _resume_quota_agent(call: ServiceCall) -> ServiceResponse:
    """Ask usage-auto-resume to resume the agent (or "all") now, like Resume now in the Paseo app."""
    agent_id: str = call.data[ATTR_AGENT_ID]
    coordinator = _coordinator(call.hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
    try:
        waiting = payload.quota(coordinator.data)[0]
        result = await coordinator.client.resume_quota(agent_id, waiting)
    except PaseoNoMatchError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="no_matching_job",
            translation_placeholders={"agent_id": agent_id},
        ) from err
    except PaseoAuthError as err:
        coordinator.config_entry.async_start_reauth(call.hass)
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="auth_failed") from err
    except PaseoDaemonError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key=err.code,
            translation_placeholders={"reason": err.reason or "unknown"},
        ) from err
    except PaseoServerError as err:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="plugin_error") from err
    except PaseoError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="cannot_connect"
        ) from err
    return result


async def _cancel_ci_run(call: ServiceCall) -> None:
    """Cancel a queued or running Paseo CI run: the plugin writes the marker the CI plugin looks for."""
    run_id: str = call.data[ATTR_RUN_ID]
    coordinator = _coordinator(call.hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
    try:
        await coordinator.client.cancel_ci_run(run_id)
    except PaseoRejectedError as err:
        if err.status >= 500:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="plugin_error"
            ) from err
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key=err.code
            if err.code in ("unknown_run", "run_finished")
            else "cancel_rejected",
            translation_placeholders={"run_id": run_id},
        ) from err
    except PaseoAuthError as err:
        coordinator.config_entry.async_start_reauth(call.hass)
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="auth_failed") from err
    except PaseoServerError as err:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="plugin_error") from err
    except PaseoError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="cannot_connect"
        ) from err


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the Paseo services."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_RESUME_QUOTA_AGENT,
        _resume_quota_agent,
        schema=RESUME_QUOTA_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CANCEL_CI_RUN,
        _cancel_ci_run,
        schema=CANCEL_CI_RUN_SCHEMA,
    )
