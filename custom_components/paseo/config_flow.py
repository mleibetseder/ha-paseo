"""Config flow: pair with the hass-bridge plugin (URL + bridge token), reconfigure, reauth."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import webhook
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .api import (
    PaseoAuthError,
    PaseoBridgeClient,
    PaseoConnectionError,
    PaseoSchemaError,
)
from .const import CONF_TOKEN, CONF_URL, CONF_WEBHOOK_ID, DOMAIN, SCHEMA_VERSION
from .webhook import async_internal_url

_LOGGER = logging.getLogger(__name__)

_TOKEN = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _schema(url: str | None = None) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_URL, default=url or vol.UNDEFINED): str,
            vol.Required(CONF_TOKEN): _TOKEN,
        }
    )


class PaseoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Paseo."""

    VERSION = 1

    async def _probe(self, url: str, token: str) -> tuple[PaseoBridgeClient, str]:
        """health (no auth) then whoami (auth); return the client and the instanceId."""
        client = PaseoBridgeClient(async_get_clientsession(self.hass), url, token)
        health = await client.health()
        if health.get("schema") != SCHEMA_VERSION:
            raise PaseoSchemaError("Unsupported schema")
        await client.whoami()
        return client, health["instanceId"]

    @staticmethod
    def _error(err: Exception) -> str:
        if isinstance(err, PaseoAuthError):
            return "invalid_auth"
        if isinstance(err, PaseoSchemaError):
            return "unsupported_schema"
        return "cannot_connect"

    def _webhook_url(self, webhook_id: str) -> str:
        return async_internal_url(self.hass, webhook_id)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the plugin URL and token, pair, create the entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].strip()
            token = user_input[CONF_TOKEN].strip()
            try:
                client, instance_id = await self._probe(url, token)
                await self.async_set_unique_id(instance_id)
                self._abort_if_unique_id_configured(updates={CONF_URL: url})
                webhook_id = webhook.async_generate_id()
                await client.register(self._webhook_url(webhook_id), push=False)
            except (PaseoAuthError, PaseoConnectionError, PaseoSchemaError) as err:
                errors["base"] = self._error(err)
            except NoURLAvailableError:
                errors["base"] = "no_internal_url"
            else:
                return self.async_create_entry(
                    title="Paseo",
                    data={
                        CONF_URL: url,
                        CONF_TOKEN: token,
                        CONF_WEBHOOK_ID: webhook_id,
                    },
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(_schema(), user_input),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the plugin URL or token and register the same webhook again."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].strip()
            token = user_input[CONF_TOKEN].strip()
            try:
                client, instance_id = await self._probe(url, token)
                await self.async_set_unique_id(instance_id)
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                await client.register(self._webhook_url(entry.data[CONF_WEBHOOK_ID]))
            except (PaseoAuthError, PaseoConnectionError, PaseoSchemaError) as err:
                errors["base"] = self._error(err)
            except NoURLAvailableError:
                errors["base"] = "no_internal_url"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_URL: url, CONF_TOKEN: token}
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                _schema(entry.data[CONF_URL]), user_input
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The token was rejected: ask for a new one."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Check the new token against the stored URL."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            try:
                _client, instance_id = await self._probe(entry.data[CONF_URL], token)
                await self.async_set_unique_id(instance_id)
                self._abort_if_unique_id_mismatch(reason="wrong_device")
            except (PaseoAuthError, PaseoConnectionError, PaseoSchemaError) as err:
                errors["base"] = self._error(err)
            else:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_TOKEN: token})
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): _TOKEN}),
            errors=errors,
        )
