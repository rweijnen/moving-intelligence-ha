"""Device tracker platform for Moving Intelligence."""
from __future__ import annotations

import logging

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MiHomeCoordinator

_LOGGER = logging.getLogger(__name__)


def build_device_info(entity_id: int, info: dict) -> DeviceInfo:
    """Build HA DeviceInfo from MI entity properties."""
    licence = info.get("license", "Unknown")
    brand = info.get("brand", "")
    model = info.get("model", "")
    name_parts = [licence]
    if brand:
        name_parts.append(brand)
    if model:
        name_parts.append(model)

    return DeviceInfo(
        identifiers={(DOMAIN, str(entity_id))},
        name=" ".join(name_parts),
        manufacturer="Moving Intelligence",
        model=info.get("objectDescription") or "Mi50",
        hw_version=info.get("hardwareSerial"),
        serial_number=info.get("hardwareSerial"),
        configuration_url="https://app.movingintelligence.com",
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up MI device tracker entities."""
    coordinator: MiHomeCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        MiDeviceTracker(coordinator, entry, eid)
        for eid in coordinator.entity_ids
    ]
    async_add_entities(entities)


class MiDeviceTracker(CoordinatorEntity[MiHomeCoordinator], TrackerEntity):
    """GPS tracker for a Moving Intelligence vehicle."""

    _attr_has_entity_name = True
    _attr_translation_key = "location"

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
        self._attr_unique_id = f"{entry.entry_id}_{licence}_tracker"
        self._attr_device_info = build_device_info(entity_id, info)

    @property
    def _live(self) -> dict:
        return (self.coordinator.data or {}).get("live", {}).get(self._mi_entity_id, {}) or {}

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        return self._live.get("latitude")

    @property
    def longitude(self) -> float | None:
        return self._live.get("longitude")

    @property
    def location_accuracy(self) -> int:
        # MI returns radius in meters (0 = GPS-precise)
        radius = self._live.get("radius")
        return int(radius) if isinstance(radius, (int, float)) else 0

    @property
    def extra_state_attributes(self) -> dict:
        live = self._live
        loc = live.get("location") or {}
        attrs: dict = {}
        if loc.get("alias"):
            attrs["alias"] = loc["alias"]
        if loc.get("road"):
            addr = loc["road"]
            if loc.get("houseNumber"):
                addr += f" {loc['houseNumber']}"
            attrs["address"] = addr
        if loc.get("city"):
            attrs["city"] = loc["city"]
        if live.get("speed") is not None:
            attrs["speed"] = live["speed"]
        if live.get("engineOn") is not None:
            attrs["engine_on"] = live["engineOn"]
        return attrs
