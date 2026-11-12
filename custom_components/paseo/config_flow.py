"""Config flow: pair with the hass-bridge plugin (URL + bridge token), reconfigure, reauth."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import webhook
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    PaseoAuthError,
    PaseoBridgeClient,
    PaseoConnectionError,
    PaseoSchemaError,
)
from .const import (
    CONF_BEDTIME_ENTITY,
    CONF_NOTIFY_TARGETS,
    CONF_QUIET_HOLD,
    CONF_REMINDER_MINUTES,
    CONF_TOKEN,
    CONF_URL,
    CONF_WEBHOOK_ID,
    DEFAULT_BEDTIME_ENTITY,
    DEFAULT_REMINDER_MINUTES,
    DOMAIN,
    QUIET_HOLD_MODES,
    QUIET_HOLD_NON_BLOCKING,
    SCHEMA_VERSION,
)
from .notifier import discover_targets
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

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PaseoOptionsFlow:
        """Notification options."""
        return PaseoOptionsFlow()

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


class PaseoOptionsFlow(OptionsFlowWithReload):
    """Notification targets, bedtime entity, reminder and quiet-hours behaviour."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose how question notifications behave; the entry reloads on save."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        found = discover_targets(self.hass)
        stored = list(options.get(CONF_NOTIFY_TARGETS, found))
        # Keep a stored target that is gone right now selectable, so saving does not drop it silently.
        choices = sorted({*found, *stored})
        bedtime = options.get(CONF_BEDTIME_ENTITY) or (
            DEFAULT_BEDTIME_ENTITY if self.hass.states.get(DEFAULT_BEDTIME_ENTITY) else None
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_NOTIFY_TARGETS, default=stored): SelectSelector(
                    SelectSelectorConfig(
                        options=choices, multiple=True, mode=SelectSelectorMode.DROPDOWN
                    )
                ),
                vol.Optional(
                    CONF_BEDTIME_ENTITY,
                    description={"suggested_value": bedtime},
                ): EntitySelector(
                    EntitySelectorConfig(domain=["input_boolean", "binary_sensor", "switch"])
                ),
                vol.Required(
                    CONF_REMINDER_MINUTES,
                    default=options.get(CONF_REMINDER_MINUTES, DEFAULT_REMINDER_MINUTES),
                ): vol.All(
                    NumberSelector(
                        NumberSelectorConfig(
                            min=0,
                            max=1440,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="min",
                        )
                    ),
                    vol.Coerce(int),
                ),
                vol.Required(
                    CONF_QUIET_HOLD,
                    default=options.get(CONF_QUIET_HOLD, QUIET_HOLD_NON_BLOCKING),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=list(QUIET_HOLD_MODES),
                        translation_key=CONF_QUIET_HOLD,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
