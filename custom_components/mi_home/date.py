"""Date platform for Moving Intelligence — journey date picker.

Provides one DateEntity per vehicle. The user changes its value to filter
which journeys are exposed via the journeys_for_date sensor.
"""
from __future__ import annotations

from datetime import date

from homeassistant.components.date import DateEntity
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
    """Set up the journey date picker for each vehicle."""
    coordinator: MiHomeCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        MiJourneyDate(coordinator, entry, eid)
        for eid in coordinator.entity_ids
    ]
    async_add_entities(entities)


class MiJourneyDate(CoordinatorEntity[MiHomeCoordinator], DateEntity):
    """Date entity that selects which day's journeys to render on the map."""

    _attr_has_entity_name = True
    _attr_translation_key = "journey_date"

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
        self._attr_unique_id = f"{entry.entry_id}_{licence}_journey_date"
        self._attr_device_info = build_device_info(entity_id, info)

    @property
    def native_value(self) -> date:
        return self.coordinator.get_selected_date(self._mi_entity_id)

    async def async_set_value(self, value: date) -> None:
        self.coordinator.set_selected_date(self._mi_entity_id, value)
