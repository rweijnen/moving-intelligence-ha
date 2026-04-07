"""Binary sensor platform for Moving Intelligence."""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MiHomeCoordinator
from .device_tracker import _device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up MI binary sensor entities."""
    coordinator: MiHomeCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = []
    for eid in coordinator.entity_ids:
        info = coordinator.entities_info.get(eid, {})
        entities.extend([
            MiEngineSensor(coordinator, entry, eid, info),
            MiJammedSensor(coordinator, entry, eid, info),
        ])
    async_add_entities(entities)


class _MiBinarySensorBase(CoordinatorEntity[MiHomeCoordinator], BinarySensorEntity):
    """Base class for MI binary sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MiHomeCoordinator,
        entry: ConfigEntry,
        entity_id: int,
        info: dict,
        key: str,
    ) -> None:
        super().__init__(coordinator)
        self._mi_entity_id = entity_id
        licence = info.get("license", "unknown").replace("-", "").lower()
        self._attr_unique_id = f"{entry.entry_id}_{licence}_{key}"
        self._attr_device_info = _device_info(entry, entity_id, info)


class MiEngineSensor(_MiBinarySensorBase):
    """Engine running binary sensor."""

    _attr_name = "Engine"
    _attr_icon = "mdi:engine"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator, entry, entity_id, info):
        super().__init__(coordinator, entry, entity_id, info, "engine")

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data or {}
        live = data.get("live", {}).get(self._mi_entity_id, {})
        return live.get("engineOn")


class MiJammedSensor(_MiBinarySensorBase):
    """Signal jammed binary sensor."""

    _attr_name = "Jammed"
    _attr_icon = "mdi:wifi-alert"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator, entry, entity_id, info):
        super().__init__(coordinator, entry, entity_id, info, "jammed")

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data or {}
        miblock = data.get("miblock", {}).get(self._mi_entity_id, {})
        if not miblock:
            return None
        return miblock.get("jammed", False)
