"""Question notifications: sticky actionable pushes, clear, reminder, bedtime hold, held list."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    EventStateChangedData,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .actions import parse_action
from .const import (
    ACTION_PREFIX,
    CONF_BEDTIME_ENTITY,
    CONF_NOTIFY_TARGETS,
    CONF_QUIET_HOLD,
    CONF_REMINDER_MINUTES,
    DEFAULT_BEDTIME_ENTITY,
    DEFAULT_REMINDER_MINUTES,
    NOTIFY_PREFIX,
    QUIET_HOLD_ALL,
    QUIET_HOLD_NEVER,
    QUIET_HOLD_NON_BLOCKING,
)
from .payload import fingerprint, well_formed_deep

if TYPE_CHECKING:
    from .coordinator import PaseoCoordinator

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
CHANNEL = "paseo"
ICON = "mdi:robot"
MAX_ACTIONS = 3
MAX_TITLE = 120  # code points
MAX_MESSAGE = 500
MAX_LABEL = 40
BLOCKING_STATES = frozenset({"stopped", "waiting"})
REMINDER_CHECK_INTERVAL = timedelta(minutes=1)
MAX_HELD = 20


def clip(text: object, limit: int) -> str:
    """Cut to `limit` code points; the last one is an ellipsis when something was cut."""
    value = text if isinstance(text, str) else ""
    return value if len(value) <= limit else value[: limit - 1] + "…"


def tag_of(question: Mapping[str, Any]) -> str:
    """The notification tag of a question."""
    return f"paseo-question-{question['key']}-{question['id']}"


def is_blocking(question: Mapping[str, Any]) -> bool:
    """Stopped and waiting questions block the agent; building ones do not."""
    return question.get("state") in BLOCKING_STATES


def discover_targets(hass: HomeAssistant) -> list[str]:
    """The legacy notify services of the mobile apps (service names, sorted)."""
    services = hass.services.async_services_for_domain("notify")
    return sorted(name for name in services if name.startswith(NOTIFY_PREFIX))


def effective_options(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """The options with their defaults; an entry whose options were never saved still notifies."""
    options = entry.options
    targets = options.get(CONF_NOTIFY_TARGETS)
    return {
        CONF_NOTIFY_TARGETS: list(targets) if targets is not None else discover_targets(hass),
        CONF_BEDTIME_ENTITY: options.get(CONF_BEDTIME_ENTITY) or DEFAULT_BEDTIME_ENTITY,
        CONF_REMINDER_MINUTES: options.get(CONF_REMINDER_MINUTES, DEFAULT_REMINDER_MINUTES),
        CONF_QUIET_HOLD: options.get(CONF_QUIET_HOLD, QUIET_HOLD_NON_BLOCKING),
    }


def message_of(question: Mapping[str, Any]) -> str:
    """The notification text (blocking or keep-building format)."""
    worktree = clip(question.get("worktree"), 60)
    qid = clip(question.get("id"), 64)
    title = clip(question.get("title"), MAX_TITLE)
    if is_blocking(question):
        text = f"⏸️ [{worktree}] Blocked on {qid}: {title} — {clip(question.get('question'), 400)}"
    else:
        recommend = clip(question.get("recommend"), 60)
        text = f"🏃 [{worktree}] Assuming {recommend} on {qid}: {title} — Tap another option to change course!"
    return clip(text, MAX_MESSAGE)


def actions_of(question: Mapping[str, Any]) -> list[dict[str, str]]:
    """Up to 3 answer buttons; the recommended option comes first, marked with a check and (Rec)."""
    options = [
        o
        for o in question.get("options") or []
        if isinstance(o, dict) and isinstance(o.get("letter"), str)
    ]
    recommend = question.get("recommend")
    options.sort(key=lambda o: o["letter"] != recommend)  # stable: Rec first, the rest in order
    result: list[dict[str, str]] = []
    for option in options:
        letter = option["letter"]
        action = (
            f"{ACTION_PREFIX}|{question['key']}|{question['id']}|{letter}|{fingerprint(question)}"
        )
        if parse_action(action) is None:
            continue
        label = clip(option.get("label"), MAX_LABEL)
        title = f"✅ {letter}: {label} (Rec)" if letter == recommend else f"{letter}: {label}"
        result.append({"action": action, "title": title})
        if len(result) == MAX_ACTIONS:
            break
    return result


class QuestionNotifier:
    """Diffs the open questions between pushes and keeps the phones in step."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, coordinator: PaseoCoordinator
    ) -> None:
        """Create the notifier; call async_start to load its state and subscribe."""
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self._store: Store[dict[str, Any]] = Store(
            hass, STORE_VERSION, f"paseo.{entry.entry_id}.notifier"
        )
        # tag -> {"at": iso, "blocking": bool, "cleared": bool}
        self.notified: dict[str, dict[str, Any]] = {}
        # tag -> question snapshot
        self.held: dict[str, dict[str, Any]] = {}
        self.reminded: set[str] = set()
        self._warned: set[str] = set()
        self._listeners: list[Callable[[], None]] = []
        self._lock = asyncio.Lock()
        self._unsubs: list[CALLBACK_TYPE] = []

    # -- lifecycle -------------------------------------------------------------------------

    async def async_start(self) -> None:
        """Load the persisted state and subscribe to pushes, the bedtime entity and the reminder timer."""
        saved = await self._store.async_load() or {}
        self.notified = {k: dict(v) for k, v in (saved.get("notified") or {}).items()}
        self.held = {k: dict(v) for k, v in (saved.get("held") or {}).items()}
        self.reminded = set(saved.get("reminded") or [])
        opts = effective_options(self.hass, self.entry)
        self._unsubs.append(self.coordinator.async_add_listener(self._on_update))
        self._unsubs.append(
            async_track_state_change_event(self.hass, [opts[CONF_BEDTIME_ENTITY]], self._on_bedtime)
        )
        if opts[CONF_REMINDER_MINUTES] > 0:
            self._unsubs.append(
                async_track_time_interval(self.hass, self._on_tick, REMINDER_CHECK_INTERVAL)
            )
        if self.coordinator.data:
            self._schedule(self._process)

    @callback
    def async_stop(self) -> None:
        """Unsubscribe; the persisted state stays."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    @callback
    def async_add_listener(self, update: Callable[[], None]) -> Callable[[], None]:
        """Call `update` whenever the held list changes (the held sensor)."""
        self._listeners.append(update)

        def _remove() -> None:
            self._listeners.remove(update)

        return _remove

    def _schedule(self, func: Callable[[], Any]) -> None:
        self.entry.async_create_task(self.hass, func())

    @callback
    def _on_update(self) -> None:
        self._schedule(self._process)

    @callback
    def _on_bedtime(self, _event: Event[EventStateChangedData]) -> None:
        self._schedule(self._remind)

    @callback
    def _on_tick(self, _now: datetime) -> None:
        self._schedule(self._remind)

    # -- state -----------------------------------------------------------------------------

    def _bedtime_on(self, opts: Mapping[str, Any]) -> bool:
        state = self.hass.states.get(opts[CONF_BEDTIME_ENTITY])
        return state is not None and state.state == "on"

    def _open_questions(self) -> list[dict[str, Any]] | None:
        """The listed open questions, or None when the push cannot be diffed (heartbeat, truncated list)."""
        data = self.coordinator.data
        section = data.get("questions") if isinstance(data, dict) else None
        if not isinstance(section, dict) or not isinstance(section.get("items"), list):
            return None
        return [
            q
            for q in section["items"]
            if isinstance(q, dict)
            and isinstance(q.get("key"), str)
            and isinstance(q.get("id"), str)
        ]

    def _truncated(self) -> bool:
        section = (self.coordinator.data or {}).get("questions")
        count = section.get("count") if isinstance(section, dict) else None
        items = section.get("items") if isinstance(section, dict) else None
        return isinstance(count, int) and isinstance(items, list) and count > len(items)

    async def _save(self) -> None:
        await self._store.async_save(
            {"notified": self.notified, "held": self.held, "reminded": sorted(self.reminded)}
        )
        for update in list(self._listeners):
            update()

    async def _process(self) -> None:
        """Diff the latest push against what was notified and held."""
        async with self._lock:
            await self._process_locked()

    async def _process_locked(self) -> set[str]:
        """The diff; the caller holds the lock. Returns the tags it sent a push for."""
        sent: set[str] = set()
        questions = self._open_questions()
        if questions is None:
            return sent
        opts = effective_options(self.hass, self.entry)
        open_tags = {tag_of(q): q for q in questions}
        changed = False

        if not self._truncated():
            for tag in [t for t in self.notified if t not in open_tags]:
                entry = self.notified.pop(tag)
                self.reminded.discard(tag)
                changed = True
                if not entry.get("cleared"):
                    await self._clear(opts, tag)
            for tag in [t for t in self.held if t not in open_tags]:
                del self.held[tag]
                changed = True

        bedtime = self._bedtime_on(opts)
        for tag, question in open_tags.items():
            try:
                changed |= await self._handle_question(opts, bedtime, tag, question, sent)
            except Exception:
                changed = True  # a mutation made before the exception must still reach the Store
                if tag not in self._warned:
                    self._warned.add(tag)
                    _LOGGER.warning(
                        "Skipping question %s: it could not be processed", tag, exc_info=True
                    )
        if changed:
            await self._save()
        return sent

    async def _handle_question(
        self,
        opts: Mapping[str, Any],
        bedtime: bool,
        tag: str,
        question: Mapping[str, Any],
        sent: set[str],
    ) -> bool:
        """Decide and act for one open question; True when the state changed."""
        changed = False
        fp = fingerprint(question)
        info = self.notified.get(tag)
        stale_push = False  # an old push of this tag is still on the phones
        if info is not None:
            if info.get("fp") == fp:
                if info.get("blocking") != is_blocking(question):
                    info["blocking"] = is_blocking(question)
                    changed = True
                return changed
            # Same tag, different question (a new run starts at Q1 again): notify it afresh.
            stale_push = not info.get("cleared")
            del self.notified[tag]
            self.reminded.discard(tag)
            changed = True
        snapshot = self.held.get(tag)
        if snapshot is not None:
            # Same question: keep it held, unless it turned blocking and only non-blocking ones are held.
            keep = snapshot.get("fp") == fp and not (
                bedtime and is_blocking(question) and not self._hold(opts, bedtime, question)
            )
            if keep:
                if snapshot.get("state") != question.get("state"):
                    self.held[tag] = {**well_formed_deep(question), "fp": fp}
                    changed = True
                return changed
            del self.held[tag]
            changed = True
        changed = True
        if self._hold(opts, bedtime, question) and len(self.held) < MAX_HELD:
            self.held[tag] = {**well_formed_deep(question), "fp": fp}
            if stale_push:
                # The old push (old text, dead buttons) must not stay when the new question is held.
                await self._clear(opts, tag)
            return changed
        self.notified[tag] = {
            "at": dt_util.utcnow().isoformat(),
            "blocking": is_blocking(question),
            "cleared": False,
            "fp": fp,
        }
        await self._send(opts, question, tag)
        sent.add(tag)
        return changed

    @staticmethod
    def _hold(opts: Mapping[str, Any], bedtime: bool, question: Mapping[str, Any]) -> bool:
        if not bedtime or opts[CONF_QUIET_HOLD] == QUIET_HOLD_NEVER:
            return False
        if opts[CONF_QUIET_HOLD] == QUIET_HOLD_ALL:
            return True
        return opts[CONF_QUIET_HOLD] == QUIET_HOLD_NON_BLOCKING and not is_blocking(question)

    async def _remind(self) -> None:
        """One reminder per blocking question that is still open after reminder_minutes (bedtime off)."""
        async with self._lock:
            opts = effective_options(self.hass, self.entry)
            minutes = opts[CONF_REMINDER_MINUTES]
            if minutes <= 0 or self._bedtime_on(opts):
                return
            open_tags = {tag_of(q): q for q in self._open_questions() or []}
            now = dt_util.utcnow()
            changed = False
            for tag, info in self.notified.items():
                question = open_tags.get(tag)
                if (
                    question is None
                    or not is_blocking(question)
                    or info.get("cleared")
                    or tag in self.reminded
                ):
                    continue
                at = dt_util.parse_datetime(info.get("at", ""))
                if at is None or now - at < timedelta(minutes=minutes):
                    continue
                self.reminded.add(tag)
                changed = True
                await self._send(opts, question, tag, reminder_minutes=minutes)
            if changed:
                await self._save()

    # -- answers ---------------------------------------------------------------------------

    async def async_answer_succeeded(self, worktree: str, question_id: str) -> None:
        """Clear the notification at once; the question stays known until the push drops it."""
        tag = f"paseo-question-{worktree}-{question_id}"
        async with self._lock:
            info = self.notified.get(tag)
            if info is None or info.get("cleared"):
                return
            info["cleared"] = True
            await self._clear(effective_options(self.hass, self.entry), tag)
            await self._save()

    async def async_answer_failed(
        self, worktree: str, question_id: str, reason: str, retry: bool
    ) -> None:
        """Say on the same tag that the answer did not go through; the buttons stay."""
        tag = f"paseo-question-{worktree}-{question_id}"
        async with self._lock:
            question = next((q for q in self._open_questions() or [] if tag_of(q) == tag), None)
            info = self.notified.get(tag)
            if question is None or info is None or info.get("cleared"):
                # Answered already (from another phone): a follow-up with buttons would never be cleared.
                return
            suffix = " — tap again" if retry else ""
            await self._send(
                effective_options(self.hass, self.entry),
                question,
                tag,
                override=f"Couldn't send: {clip(reason, 80)}{suffix}",
            )

    async def async_resend(self, worktree: str, question_id: str) -> None:
        """A tap was stale: bring the phones in line with the last push, then show the question again."""
        tag = f"paseo-question-{worktree}-{question_id}"
        async with self._lock:
            # Apply the push first (holds, fingerprints, reminders); resend only what it did not just send.
            sent = await self._process_locked()
            info = self.notified.get(tag)
            question = self.question(worktree, question_id)
            if tag in sent or question is None or info is None or info.get("cleared"):
                return
            await self._send(effective_options(self.hass, self.entry), question, tag)

    def question(self, worktree: str, question_id: str) -> dict[str, Any] | None:
        """The question of the last push with this key and id."""
        return next(
            (
                q
                for q in self._open_questions() or []
                if q["key"] == worktree and q["id"] == question_id
            ),
            None,
        )

    # -- delivery --------------------------------------------------------------------------

    async def _send(
        self,
        opts: Mapping[str, Any],
        question: Mapping[str, Any],
        tag: str,
        *,
        reminder_minutes: int | None = None,
        override: str | None = None,
    ) -> None:
        message = override or message_of(question)
        title = "Paseo question"
        if reminder_minutes is not None:
            title = "Paseo reminder"
            message = clip(f"⏰ Still open after {reminder_minutes} min. {message}", MAX_MESSAGE)
        data: dict[str, Any] = {
            "tag": tag,
            "sticky": "true",
            "channel": CHANNEL,
            "notification_icon": ICON,
            "actions": actions_of(question),
        }
        await self._call(opts, {"title": title, "message": message, "data": data})

    async def _clear(self, opts: Mapping[str, Any], tag: str) -> None:
        await self._call(opts, {"message": "clear_notification", "data": {"tag": tag}})

    async def _call(self, opts: Mapping[str, Any], service_data: dict[str, Any]) -> None:
        """Call every target on its own; a missing or failing target is skipped."""
        for target in opts[CONF_NOTIFY_TARGETS]:
            if not self.hass.services.has_service("notify", target):
                if target not in self._warned:
                    self._warned.add(target)
                    _LOGGER.warning(
                        "The notify service notify.%s does not exist; skipping it", target
                    )
                continue
            try:
                await self.hass.services.async_call(
                    "notify", target, dict(service_data), blocking=True
                )
            except Exception:
                _LOGGER.warning("Sending to notify.%s failed", target, exc_info=True)

    # -- brief -----------------------------------------------------------------------------

    def brief(self) -> dict[str, Any]:
        """The held questions plus the open blocking ones, and a ready-made one-line text."""
        found: dict[str, dict[str, Any]] = {t: dict(q) for t, q in self.held.items()}
        for q in self._open_questions() or []:
            if is_blocking(q):
                found.setdefault(tag_of(q), dict(q))
        items = [
            {
                "worktree": q.get("worktree"),
                "key": q.get("key"),
                "id": q.get("id"),
                "title": q.get("title"),
                "question": q.get("question"),
                "state": q.get("state"),
                "recommend": q.get("recommend"),
                "held": tag in self.held,
            }
            for tag, q in found.items()
        ]
        if not items:
            return {"count": 0, "questions": [], "text": "No Paseo questions are waiting for you."}
        parts = [
            f"[{clip(i['worktree'], 30)}] {clip(i['id'], 20)}: {clip(i['title'], 60)}"
            for i in items[:5]
        ]
        more = f" and {len(items) - 5} more" if len(items) > 5 else ""
        noun = "question is" if len(items) == 1 else "questions are"
        return {
            "count": len(items),
            "questions": items,
            "text": clip(
                f"{len(items)} Paseo {noun} waiting: " + "; ".join(parts) + more + ".", 500
            ),
        }
