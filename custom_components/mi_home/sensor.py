"""Sensor platform for Moving Intelligence."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfSpeed, UnitOfElectricPotential
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
    """Set up MI sensor entities."""
    coordinator: MiHomeCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []
    for eid in coordinator.entity_ids:
        info = coordinator.entities_info.get(eid, {})
        entities.extend([
            MiSpeedSensor(coordinator, entry, eid, info),
            MiAddressSensor(coordinator, entry, eid, info),
            MiBatterySensor(coordinator, entry, eid, info),
            MiLastJourneyDistanceSensor(coordinator, entry, eid, info),
            MiLastJourneyDurationSensor(coordinator, entry, eid, info),
            MiAlarmCountSensor(coordinator, entry, eid, info),
        ])
    async_add_entities(entities)


class _MiSensorBase(CoordinatorEntity[MiHomeCoordinator], SensorEntity):
    """Base class for MI sensors."""

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

    @property
    def _live(self) -> dict:
        data = self.coordinator.data or {}
        return data.get("live", {}).get(self._mi_entity_id, {})


class MiSpeedSensor(_MiSensorBase):
    """Current speed sensor."""

    _attr_name = "Speed"
    _attr_icon = "mdi:speedometer"
    _attr_native_unit_of_measurement = UnitOfSpeed.KILOMETERS_PER_HOUR
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry, entity_id, info):
        super().__init__(coordinator, entry, entity_id, info, "speed")

    @property
    def native_value(self) -> int | None:
        return self._live.get("speed")


class MiAddressSensor(_MiSensorBase):
    """Current address sensor."""

    _attr_name = "Address"
    _attr_icon = "mdi:map-marker"

    def __init__(self, coordinator, entry, entity_id, info):
        super().__init__(coordinator, entry, entity_id, info, "address")

    @property
    def native_value(self) -> str | None:
        loc = self._live.get("location", {})
        if not loc:
            return None
        if loc.get("alias"):
            return loc["alias"]
        parts = []
        if loc.get("road"):
            road = loc["road"]
            if loc.get("houseNumber"):
                road += f" {loc['houseNumber']}"
            parts.append(road)
        if loc.get("city"):
            parts.append(loc["city"])
        return ", ".join(parts) if parts else None

    @property
    def extra_state_attributes(self) -> dict:
        loc = self._live.get("location", {})
        attrs = {}
        for key in ("road", "houseNumber", "postalCode", "city", "country", "alias"):
            if loc.get(key):
                attrs[key] = loc[key]
        return attrs


class MiBatterySensor(_MiSensorBase):
    """Battery voltage sensor."""

    _attr_name = "Battery voltage"
    _attr_icon = "mdi:car-battery"
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry, entity_id, info):
        super().__init__(coordinator, entry, entity_id, info, "battery")

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data or {}
        return data.get("battery", {}).get(self._mi_entity_id)


class MiLastJourneyDistanceSensor(_MiSensorBase):
    """Last journey distance sensor."""

    _attr_name = "Last journey distance"
    _attr_icon = "mdi:map-marker-distance"
    _attr_native_unit_of_measurement = "km"
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, coordinator, entry, entity_id, info):
        super().__init__(coordinator, entry, entity_id, info, "last_journey_distance")

    @property
    def native_value(self) -> float | None:
        journeys = self.coordinator.get_journeys(self._mi_entity_id)
        if not journeys:
            return None
        return journeys[-1].get("distance_km")

    @property
    def extra_state_attributes(self) -> dict:
        journeys = self.coordinator.get_journeys(self._mi_entity_id)
        if not journeys:
            return {}
        j = journeys[-1]
        attrs = {
            "max_speed": j.get("max_speed"),
            "avg_speed": j.get("avg_speed"),
            "waypoint_count": j.get("waypoint_count"),
            "total_journeys_stored": len(journeys),
        }
        if j.get("start_time"):
            attrs["start_time"] = datetime.fromtimestamp(
                j["start_time"], tz=timezone.utc
            ).isoformat()
        if j.get("end_time"):
            attrs["end_time"] = datetime.fromtimestamp(
                j["end_time"], tz=timezone.utc
            ).isoformat()
        return attrs


class MiLastJourneyDurationSensor(_MiSensorBase):
    """Last journey duration sensor."""

    _attr_name = "Last journey duration"
    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = "min"

    def __init__(self, coordinator, entry, entity_id, info):
        super().__init__(coordinator, entry, entity_id, info, "last_journey_duration")

    @property
    def native_value(self) -> int | None:
        journeys = self.coordinator.get_journeys(self._mi_entity_id)
        if not journeys:
            return None
        j = journeys[-1]
        start = j.get("start_time", 0)
        end = j.get("end_time", 0)
        if start and end:
            return round((end - start) / 60)
        return None


class MiAlarmCountSensor(_MiSensorBase):
    """Alarm message count sensor."""

    _attr_name = "Alarm count"
    _attr_icon = "mdi:alarm-light"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry, entity_id, info):
        super().__init__(coordinator, entry, entity_id, info, "alarm_count")

    @property
    def native_value(self) -> int:
        data = self.coordinator.data or {}
        return len(data.get("alarms", []))
