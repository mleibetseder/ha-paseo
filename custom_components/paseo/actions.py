"""Notification actions: a tap on `PASEO_ANSWER|<worktreeKey>|<questionId>|<letter>` answers a question."""

from __future__ import annotations

from collections.abc import Callable
import logging
import re

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import ACTION_PREFIX
from .coordinator import PaseoCoordinator
from .payload import fingerprint
from .services import async_answer_question

_LOGGER = logging.getLogger(__name__)

EVENT_ACTION = "mobile_app_notification_action"
# The same character sets the plugin accepts (REST API contract); fullmatch, so no trailing newline slips through.
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")
_QUESTION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_LETTER = re.compile(r"[A-Z]")
_FINGERPRINT = re.compile(r"[0-9a-f]{8}")
# Short words for the follow-up notification when a tapped answer did not go through.
FAILURE_REASONS = {
    "answer_agent_unreachable": "agent unreachable",
    "answer_daemon_unavailable": "Paseo daemon unavailable",
    "cannot_connect": "plugin unreachable",
    "plugin_error": "plugin error",
    "auth_failed": "plugin rejected the token",
    "already_answered": "already answered",
    "unknown_question": "question is no longer open",
    "answer_in_progress": "answer in progress elsewhere",
    "answers_off": "answers are switched off",
}


def parse_action(action: object) -> tuple[str, str, str, str] | None:
    """(worktree, question id, letter, fingerprint) of a PASEO_ANSWER action, or None for anything else."""
    if not isinstance(action, str):
        return None
    parts = action.split("|")
    if len(parts) != 5 or parts[0] != ACTION_PREFIX:
        return None
    _, worktree, question_id, letter, fp = parts
    if (
        _KEY.fullmatch(worktree)
        and _QUESTION_ID.fullmatch(question_id)
        and _LETTER.fullmatch(letter)
        and _FINGERPRINT.fullmatch(fp)
    ):
        return worktree, question_id, letter, fp
    return None


def _find(coordinator: PaseoCoordinator, worktree: str, question_id: str) -> dict | None:
    """The question in this entry's last payload (questions.items: key, id)."""
    data = coordinator.data
    questions = data.get("questions") if isinstance(data, dict) else None
    items = questions.get("items") if isinstance(questions, dict) else None
    if not isinstance(items, list):
        return None
    return next(
        (
            item
            for item in items
            if isinstance(item, dict)
            and item.get("key") == worktree
            and item.get("id") == question_id
        ),
        None,
    )


@callback
def async_listen_for_actions(
    hass: HomeAssistant, coordinator: PaseoCoordinator
) -> Callable[[], None]:
    """Answer on a tapped action of this entry's questions; returns the unsubscribe."""

    async def _handle(event: Event) -> None:
        action = event.data.get("action")
        parsed = parse_action(action)
        if parsed is None:
            if isinstance(action, str) and action.startswith(f"{ACTION_PREFIX}|"):
                _LOGGER.debug("Ignoring a malformed %s action", ACTION_PREFIX)
            return
        question = _find(coordinator, parsed[0], parsed[1])
        if question is None:
            # Another Paseo entry's question, or one this entry's last push did not list.
            _LOGGER.debug(
                "Ignoring a %s action for a question this entry does not list", ACTION_PREFIX
            )
            return
        notifier = coordinator.notifier
        if fingerprint(question) != parsed[3]:
            # The buttons belong to an older question that reused this id: never answer the new one with them.
            _LOGGER.debug(
                "Ignoring a %s action for a question whose content changed", ACTION_PREFIX
            )
            if notifier is not None:
                await notifier.async_resend(parsed[0], parsed[1])
            return
        try:
            await async_answer_question(hass, coordinator, *parsed[:3], None, parsed[3])
        except HomeAssistantError as err:
            _LOGGER.debug("The tapped answer was not accepted: %s", err)
            if err.translation_key == "question_changed":
                # The plugin holds a newer question under this id than our last push: show the current one.
                if notifier is not None:
                    await notifier.async_resend(parsed[0], parsed[1])
            elif notifier is not None:
                await notifier.async_answer_failed(
                    parsed[0],
                    parsed[1],
                    FAILURE_REASONS.get(err.translation_key or "", "the answer was refused"),
                    retry=not isinstance(err, ServiceValidationError),
                )
        else:
            if notifier is not None:
                await notifier.async_answer_succeeded(parsed[0], parsed[1])

    return hass.bus.async_listen(EVENT_ACTION, _handle)
