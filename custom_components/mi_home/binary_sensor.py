"""Binary sensor platform for Moving Intelligence."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MiHomeCoordinator
from .device_tracker import build_device_info


@dataclass(frozen=True, kw_only=True)
class MiBinarySensorDescription(BinarySensorEntityDescription):
    """Description for an MI binary sensor."""

    is_on_fn: Callable[[MiHomeCoordinator, int], bool | None]


def _live(coord: MiHomeCoordinator, eid: int) -> dict:
    return (coord.data or {}).get("live", {}).get(eid, {}) or {}


def _miblock(coord: MiHomeCoordinator, eid: int) -> dict:
    return (coord.data or {}).get("miblock", {}).get(eid, {}) or {}


BINARY_SENSOR_DESCRIPTIONS: tuple[MiBinarySensorDescription, ...] = (
    MiBinarySensorDescription(
        key="engine",
        translation_key="engine",
        device_class=BinarySensorDeviceClass.RUNNING,
        is_on_fn=lambda c, e: _live(c, e).get("engineOn"),
    ),
    MiBinarySensorDescription(
        key="jammed",
        translation_key="jammed",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda c, e: (
            _miblock(c, e).get("jammed") if _miblock(c, e) else None
        ),
    ),
)


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
        for desc in BINARY_SENSOR_DESCRIPTIONS:
            entities.append(MiBinarySensor(coordinator, entry, eid, info, desc))
    async_add_entities(entities)


class MiBinarySensor(CoordinatorEntity[MiHomeCoordinator], BinarySensorEntity):
    """Generic MI binary sensor driven by entity description."""

    _attr_has_entity_name = True
    entity_description: MiBinarySensorDescription

    def __init__(
        self,
        coordinator: MiHomeCoordinator,
        entry: ConfigEntry,
        entity_id: int,
        info: dict,
        description: MiBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._mi_entity_id = entity_id
        licence = info.get("license", "unknown").replace("-", "").lower()
        self._attr_unique_id = f"{entry.entry_id}_{licence}_{description.key}"
        self._attr_device_info = build_device_info(entity_id, info)

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.is_on_fn(
            self.coordinator, self._mi_entity_id
        )
