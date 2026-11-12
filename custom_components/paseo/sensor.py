"""Sensors."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import PaseoConfigEntry, payload
from .coordinator import PaseoCoordinator
from .entity import PaseoEntity, PaseoRunEntity, ensure_hub_device, run_unique_id

PARALLEL_UPDATES = 0  # read-only entities fed by pushes


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PaseoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            PaseoLastPushSensor(coordinator),
            PaseoHeldQuestionsSensor(coordinator),
            *(PaseoProgressSensor(coordinator, d) for d in PROGRESS_SENSORS),
        ]
    )

    live: dict[str, PaseoRunEntity] = {}  # unique id -> entity added by this setup

    @callback
    def _add_new_runs() -> None:
        """Add the run sensors that are not live yet: new runs, and ones the user deleted. Gone runs keep theirs."""
        hub_id: str | None = None
        entities: list[PaseoRunSensor] = []
        for key in payload.run_keys(coordinator.data):
            for description in RUN_SENSORS:
                if run_unique_id(coordinator.instance_id, key, description.key) in live:
                    continue
                hub_id = hub_id or ensure_hub_device(hass, coordinator)
                worktree = payload.worktree(coordinator.data, key)
                entity = PaseoRunSensor(coordinator, key, worktree, hub_id, description)
                entity.live = live
                live[entity.unique_id] = entity
                entities.append(entity)
        if entities:
            async_add_entities(entities)

    _add_new_runs()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_runs))


class PaseoHeldQuestionsSensor(PaseoEntity, SensorEntity):
    """Questions the notifier held back (quiet hours), until they are answered or gone."""

    _unrecorded_attributes = frozenset({"questions"})

    def __init__(self, coordinator: PaseoCoordinator) -> None:
        """Create the sensor."""
        super().__init__(coordinator, "held_questions")

    async def async_added_to_hass(self) -> None:
        """Follow the notifier, which changes the list after the coordinator listeners ran."""
        await super().async_added_to_hass()
        if self.coordinator.notifier is not None:
            self.async_on_remove(
                self.coordinator.notifier.async_add_listener(self.async_write_ha_state)
            )

    @property
    def available(self) -> bool:
        """The held list is local state; it stays readable while the bridge is offline."""
        return self.coordinator.notifier is not None

    @property
    def native_value(self) -> int:
        """Return the number of held questions."""
        notifier = self.coordinator.notifier
        return len(notifier.held) if notifier else 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the held questions."""
        notifier = self.coordinator.notifier
        return {"questions": list(notifier.held.values()) if notifier else []}


class PaseoLastPushSensor(PaseoEntity, SensorEntity):
    """When the plugin last pushed. Stays readable while the bridge is offline."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: PaseoCoordinator) -> None:
        """Create the sensor."""
        super().__init__(coordinator, "last_push")

    @property
    def available(self) -> bool:
        """Unavailable only until the first push; afterwards the last time is the answer."""
        return self.coordinator.last_push is not None

    @property
    def native_value(self) -> datetime | None:
        """Return the pushed_at of the last accepted payload."""
        return self.coordinator.last_push


@dataclass(frozen=True, kw_only=True)
class PaseoSensorDescription(SensorEntityDescription):
    """A sensor computed from the last payload."""

    value_fn: Callable[[Mapping[str, Any]], str | int | float | datetime | None]
    attrs_fn: Callable[[Mapping[str, Any]], dict[str, Any]] = lambda _: {}
    available_fn: Callable[[Mapping[str, Any]], bool] = lambda _: True


def _active_ticket_value(data: Mapping[str, Any]) -> str | None:
    ticket = payload.active_ticket(data)
    if ticket is None:
        return None
    return str(ticket["id"])[: payload.MAX_STATE_LENGTH]


def _active_ticket_attrs(data: Mapping[str, Any]) -> dict[str, Any]:
    ticket = payload.active_ticket(data)
    if ticket is None:
        return {}
    keys = ("id", "title", "stage", "elapsed_min", "estimate_min", "worktree")
    return {key: ticket.get(key) for key in keys}


def _calibrated_attrs(data: Mapping[str, Any]) -> dict[str, Any]:
    section = payload.eta(data)
    finish = payload.finish_at(data)
    return {
        "factor": payload.eta_number(data, "factor"),
        "samples": payload.eta_number(data, "samples"),
        "calibrated": section.get("calibrated") is True,
        "finish_at": finish.isoformat() if finish else None,
        "worktree": section.get("worktree"),
    }


def _quota_attrs(data: Mapping[str, Any]) -> dict[str, Any]:
    _, next_due, jobs = payload.quota(data)
    return {"next_due_at": next_due.isoformat() if next_due else None, "jobs": jobs}


PROGRESS_SENSORS: tuple[PaseoSensorDescription, ...] = (
    PaseoSensorDescription(
        key="ci_queue",
        translation_key="ci_queue",
        device_class=SensorDeviceClass.ENUM,
        options=list(payload.CI_STATES),
        value_fn=lambda data: (payload.ci(data) or {}).get("state"),
        attrs_fn=payload.ci_attrs,
        available_fn=lambda data: payload.ci(data) is not None,
    ),
    PaseoSensorDescription(
        key="active_agents",
        translation_key="active_agents",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: payload.agents(data)[0],
        attrs_fn=lambda data: {"agents": payload.agents(data)[1]},
    ),
    PaseoSensorDescription(
        key="quota_waiting",
        translation_key="quota_waiting",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: payload.quota(data)[0],
        attrs_fn=_quota_attrs,
    ),
    PaseoSensorDescription(
        key="current_activity",
        translation_key="current_activity",
        value_fn=payload.ticker,
    ),
    PaseoSensorDescription(
        key="active_ticket",
        translation_key="active_ticket",
        value_fn=_active_ticket_value,
        attrs_fn=_active_ticket_attrs,
    ),
    PaseoSensorDescription(
        key="run_progress",
        translation_key="run_progress",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: payload.eta_number(data, "progress_pct"),
    ),
    PaseoSensorDescription(
        key="raw_eta",
        translation_key="raw_eta",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: payload.eta_number(data, "raw_eta_min"),
    ),
    PaseoSensorDescription(
        key="calibrated_eta",
        translation_key="calibrated_eta",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: payload.eta_number(data, "calibrated_eta_min"),
        attrs_fn=_calibrated_attrs,
    ),
    PaseoSensorDescription(
        key="calibrated_finish",
        translation_key="calibrated_finish",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=payload.finish_at,
    ),
    PaseoSensorDescription(
        key="deliverables_count",
        translation_key="deliverables_count",
        value_fn=lambda data: payload.deliverables(data)[0],
        attrs_fn=lambda data: {"items": payload.deliverables(data)[1]},
    ),
    PaseoSensorDescription(
        key="open_questions",
        translation_key="open_questions",
        value_fn=lambda data: payload.open_questions(data)[0],
        attrs_fn=lambda data: {"questions": payload.open_questions(data)[1]},
    ),
    PaseoSensorDescription(
        key="projects_overview",
        translation_key="projects_overview",
        value_fn=lambda data: sum(not payload.is_idle(p) for p in payload.overview(data)),
        attrs_fn=lambda data: {
            "projects": payload.overview(data),
            "idle": sum(payload.is_idle(p) for p in payload.overview(data)),
        },
    ),
)


class PaseoProgressSensor(PaseoEntity, SensorEntity):
    """A progress sensor; unavailable until the first push and whenever the data is stale."""

    entity_description: PaseoSensorDescription
    # The lists can be long and change often; the recorder does not need them.
    _unrecorded_attributes = frozenset(
        {"items", "questions", "projects", "agents", "jobs", "rows"}
    )  # "idle" is a count and is kept

    def __init__(self, coordinator: PaseoCoordinator, description: PaseoSensorDescription) -> None:
        """Create the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Unavailable when the data is stale, and for the CI queue while the Paseo CI plugin is not alive."""
        return super().available and self.entity_description.available_fn(
            self.coordinator.data or {}
        )

    @property
    def native_value(self) -> str | int | float | datetime | None:
        """Return the value computed from the last payload."""
        return self.entity_description.value_fn(self.coordinator.data or {})

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the attributes computed from the last payload."""
        return self.entity_description.attrs_fn(self.coordinator.data or {})


def _run_active_ticket_attrs(view: Mapping[str, Any]) -> dict[str, Any]:
    ticket = payload.run_of(view).get("active_ticket")
    if not isinstance(ticket, dict) or not ticket.get("id"):
        return {}
    return {k: ticket.get(k) for k in ("id", "title", "stage", "elapsed_min", "estimate_min")}


def _run_active_ticket_value(view: Mapping[str, Any]) -> str | None:
    ticket = payload.run_of(view).get("active_ticket")
    if not isinstance(ticket, dict) or not ticket.get("id"):
        return None
    return str(ticket["id"])[: payload.MAX_STATE_LENGTH]


# The entity name decides the entity id: sensor.paseo_<worktree>_<name>.
RUN_SENSORS: tuple[PaseoSensorDescription, ...] = (
    PaseoSensorDescription(
        key="project_progress",
        translation_key="project_progress",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda view: payload.eta_number(view, "progress_pct"),
    ),
    PaseoSensorDescription(
        key="project_calibrated_eta",
        translation_key="project_calibrated_eta",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda view: payload.eta_number(view, "calibrated_eta_min"),
        attrs_fn=_calibrated_attrs,
    ),
    PaseoSensorDescription(
        key="project_calibrated_finish",
        translation_key="project_calibrated_finish",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=payload.finish_at,
    ),
    PaseoSensorDescription(
        key="project_active_ticket",
        translation_key="project_active_ticket",
        value_fn=_run_active_ticket_value,
        attrs_fn=_run_active_ticket_attrs,
    ),
    PaseoSensorDescription(
        key="project_open_questions",
        translation_key="project_open_questions",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda view: payload.run_count(view, "open_questions"),
        attrs_fn=lambda view: {"questions": payload.run_listed(view, "open_questions")},
    ),
    PaseoSensorDescription(
        key="project_stuck",
        translation_key="project_stuck",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda view: payload.run_count(view, "stuck"),
        attrs_fn=lambda view: {"items": payload.run_listed(view, "stuck")},
    ),
    PaseoSensorDescription(
        key="project_deliverables",
        translation_key="project_deliverables",
        value_fn=lambda view: payload.deliverables(view)[0],
        attrs_fn=lambda view: {"items": payload.deliverables(view)[1]},
    ),
    PaseoSensorDescription(
        key="project_last_update",
        translation_key="project_last_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=payload.run_last_update,
    ),
)


class PaseoRunSensor(PaseoRunEntity, SensorEntity):
    """A sensor of one run; the description's functions read the run view."""

    entity_description: PaseoSensorDescription
    _unrecorded_attributes = frozenset({"items", "questions"})

    def __init__(
        self,
        coordinator: PaseoCoordinator,
        run_key: str,
        worktree: str,
        hub_id: str,
        description: PaseoSensorDescription,
    ) -> None:
        """Create the sensor."""
        super().__init__(coordinator, run_key, description.key, worktree, hub_id)
        self.entity_description = description

    @property
    def native_value(self) -> str | int | float | datetime | None:
        """Return the value computed from the run."""
        view = self.run_view
        return self.entity_description.value_fn(view) if view else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the run's key and worktree and the description's attributes."""
        view = self.run_view
        attrs = self.entity_description.attrs_fn(view) if view else {}
        return {**attrs, **self.run_attributes()}
