"""Calendar platform for Moving Intelligence — journey history."""
from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MiHomeCoordinator
from .device_tracker import build_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the journey calendar for each vehicle."""
    coordinator: MiHomeCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        MiJourneyCalendar(coordinator, entry, eid)
        for eid in coordinator.entity_ids
    ]
    async_add_entities(entities)


def _journey_to_event(mi_entity_id: int, journey: dict) -> CalendarEvent:
    """Convert a stored journey dict to an HA CalendarEvent."""
    start = datetime.fromtimestamp(journey["start_time"], tz=timezone.utc)
    end = datetime.fromtimestamp(journey["end_time"], tz=timezone.utc)
    distance = journey.get("distance_km", 0)
    duration_min = round((journey["end_time"] - journey["start_time"]) / 60)
    max_speed = journey.get("max_speed", "?")
    avg_speed = journey.get("avg_speed", "?")
    waypoints = journey.get("waypoint_count", 0)

    summary = f"{distance} km · {duration_min} min"

    description_lines = [
        f"Distance: {distance} km",
        f"Duration: {duration_min} min",
        f"Max speed: {max_speed} km/h",
        f"Avg speed: {avg_speed} km/h",
        f"Waypoints: {waypoints}",
    ]

    start_loc = journey.get("start_location") or {}
    end_loc = journey.get("end_location") or {}
    if start_loc.get("lat") is not None and end_loc.get("lat") is not None:
        maps_url = (
            f"https://www.google.com/maps/dir/"
            f"{start_loc['lat']},{start_loc['lon']}/"
            f"{end_loc['lat']},{end_loc['lon']}"
        )
        description_lines.append(f"Route preview: {maps_url}")

    return CalendarEvent(
        start=start,
        end=end,
        summary=summary,
        description="\n".join(description_lines),
        uid=f"mi_{mi_entity_id}_{journey['start_time']}",
    )


class MiJourneyCalendar(CoordinatorEntity[MiHomeCoordinator], CalendarEntity):
    """Calendar showing past journeys for a vehicle."""

    _attr_has_entity_name = True
    _attr_translation_key = "journeys"

    def __init__(
        self,
        coordinator: MiHomeCoordinator,
        entry: ConfigEntry,
        entity_id: int,
    ) -> None:
        super().__init__(coordinator)
        self._mi_entity_id = entity_id
        info = coordinator.entities_info.get(entity_id, {})
        licence = info.get("license", "unknown").replace("-", "").lower()
        self._attr_unique_id = f"{entry.entry_id}_{licence}_journeys"
        self._attr_device_info = build_device_info(entity_id, info)

    @property
    def event(self) -> CalendarEvent | None:
        """Return the next/current event (i.e. the most recent journey)."""
        journeys = self.coordinator.get_journeys(self._mi_entity_id)
        if not journeys:
            return None
        return _journey_to_event(self._mi_entity_id, journeys[-1])

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return all journeys whose duration overlaps the requested range."""
        journeys = self.coordinator.get_journeys(self._mi_entity_id)
        events: list[CalendarEvent] = []
        for journey in journeys:
            start_ts = journey.get("start_time")
            end_ts = journey.get("end_time")
            if not start_ts or not end_ts:
                continue
            start = datetime.fromtimestamp(start_ts, tz=timezone.utc)
            end = datetime.fromtimestamp(end_ts, tz=timezone.utc)
            if end < start_date or start > end_date:
                continue
            events.append(_journey_to_event(self._mi_entity_id, journey))
        return events
