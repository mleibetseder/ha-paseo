"""Webhook that receives the plugin's pushes."""

from __future__ import annotations

from http import HTTPStatus
import logging

from aiohttp import web

from homeassistant.components import webhook
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import PaseoCoordinator

_LOGGER = logging.getLogger(__name__)


def async_register_webhook(
    hass: HomeAssistant, webhook_id: str, coordinator: PaseoCoordinator
) -> None:
    """Register the push receiver: POST only, never reachable from outside the LAN."""

    async def _handle(hass: HomeAssistant, webhook_id: str, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except ValueError:
            _LOGGER.debug("Ignoring a webhook body that is not JSON")
            return web.Response(status=HTTPStatus.BAD_REQUEST)
        if not coordinator.async_handle_push(payload):
            return web.Response(status=HTTPStatus.BAD_REQUEST)
        return web.Response(status=HTTPStatus.OK)

    webhook.async_register(
        hass,
        DOMAIN,
        "Paseo",
        webhook_id,
        _handle,
        local_only=True,
        allowed_methods=["POST"],
    )


def async_internal_url(hass: HomeAssistant, webhook_id: str) -> str:
    """The URL the plugin pushes to: the internal (LAN) URL, because the hook is local only."""
    return webhook.async_generate_url(
        hass, webhook_id, allow_external=False, prefer_external=False, allow_ip=True
    )


def async_unregister_webhook(hass: HomeAssistant, webhook_id: str) -> None:
    """Remove the push receiver."""
    webhook.async_unregister(hass, webhook_id)
