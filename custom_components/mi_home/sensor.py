"""Sensor platform for Moving Intelligence."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricPotential, UnitOfSpeed
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MiHomeCoordinator
from .device_tracker import build_device_info


@dataclass(frozen=True, kw_only=True)
class MiSensorDescription(SensorEntityDescription):
    """Description for an MI sensor."""

    value_fn: Callable[[MiHomeCoordinator, int], Any]
    attrs_fn: Callable[[MiHomeCoordinator, int], dict[str, Any]] | None = None


def _live(coord: MiHomeCoordinator, eid: int) -> dict:
    return (coord.data or {}).get("live", {}).get(eid, {}) or {}


def _location(coord: MiHomeCoordinator, eid: int) -> dict:
    return _live(coord, eid).get("location", {}) or {}


def _format_address(coord: MiHomeCoordinator, eid: int) -> str | None:
    loc = _location(coord, eid)
    if not loc:
        return None
    if loc.get("alias"):
        return loc["alias"][:255]
    parts: list[str] = []
    if loc.get("road"):
        road = loc["road"]
        if loc.get("houseNumber"):
            road = f"{road} {loc['houseNumber']}"
        parts.append(road)
    if loc.get("city"):
        parts.append(loc["city"])
    return (", ".join(parts) or None) and ", ".join(parts)[:255]


def _address_attrs(coord: MiHomeCoordinator, eid: int) -> dict[str, Any]:
    loc = _location(coord, eid)
    return {
        k: loc[k]
        for k in ("road", "houseNumber", "postalCode", "city", "country", "alias")
        if loc.get(k)
    }


def _last_journey(coord: MiHomeCoordinator, eid: int) -> dict | None:
    journeys = coord.get_journeys(eid)
    return journeys[-1] if journeys else None


def _journey_distance(coord: MiHomeCoordinator, eid: int) -> float | None:
    j = _last_journey(coord, eid)
    return j.get("distance_km") if j else None


def _journey_duration(coord: MiHomeCoordinator, eid: int) -> int | None:
    j = _last_journey(coord, eid)
    if not j:
        return None
    start = j.get("start_time", 0)
    end = j.get("end_time", 0)
    return round((end - start) / 60) if start and end else None


def _journey_max_speed(coord: MiHomeCoordinator, eid: int) -> int | None:
    j = _last_journey(coord, eid)
    return j.get("max_speed") if j else None


def _journey_avg_speed(coord: MiHomeCoordinator, eid: int) -> int | None:
    j = _last_journey(coord, eid)
    return j.get("avg_speed") if j else None


# --- Date-filtered journey rendering ---

# HA enforces a 16KB limit per state attribute. We budget 12KB for the
# GeoJSON FeatureCollection, leaving headroom for the rest of the state.
_GEOJSON_BUDGET_BYTES = 12_000
_BYTES_PER_COORD = 23  # rough size of "[6.123456,51.123456],"
_BYTES_PER_FEATURE_OVERHEAD = 250  # type/properties wrapper


def _downsample(waypoints: list[dict], target: int) -> list[dict]:
    """Reduce a waypoint list to roughly target points by uniform sampling."""
    n = len(waypoints)
    if n <= target or target < 2:
        return waypoints
    step = (n - 1) / (target - 1)
    return [waypoints[round(i * step)] for i in range(target)]


def _journeys_for_date(coord: MiHomeCoordinator, eid: int) -> list[dict]:
    target = coord.get_selected_date(eid)
    return coord.get_journeys_on_date(eid, target)


def _journeys_for_date_count(coord: MiHomeCoordinator, eid: int) -> int:
    return len(_journeys_for_date(coord, eid))


def _journeys_for_date_attrs(
    coord: MiHomeCoordinator, eid: int
) -> dict[str, Any]:
    target = coord.get_selected_date(eid)
    journeys = _journeys_for_date(coord, eid)
    attrs: dict[str, Any] = {
        "selected_date": target.isoformat(),
        "journey_count": len(journeys),
    }
    if not journeys:
        attrs["geojson"] = {"type": "FeatureCollection", "features": []}
        return attrs

    # Decide how many waypoints each feature can carry, given the budget.
    n = len(journeys)
    available = _GEOJSON_BUDGET_BYTES - n * _BYTES_PER_FEATURE_OVERHEAD
    if available < 100:
        # Too many journeys for the budget — degrade gracefully.
        max_points = 10
    else:
        max_points = max(20, min(300, available // (n * _BYTES_PER_COORD)))

    features = []
    for j in journeys:
        wps = j.get("waypoints") or []
        if len(wps) < 2:
            continue
        sampled = _downsample(wps, max_points)
        coords = [
            [round(w["lon"], 6), round(w["lat"], 6)]
            for w in sampled
            if w.get("lat") is not None and w.get("lon") is not None
        ]
        if len(coords) < 2:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "start_time": (
                    datetime.fromtimestamp(j["start_time"], tz=timezone.utc).isoformat()
                    if j.get("start_time") else None
                ),
                "end_time": (
                    datetime.fromtimestamp(j["end_time"], tz=timezone.utc).isoformat()
                    if j.get("end_time") else None
                ),
                "distance_km": j.get("distance_km"),
                "max_speed": j.get("max_speed"),
                "avg_speed": j.get("avg_speed"),
                "waypoint_count": len(coords),
            },
        })

    attrs["geojson"] = {"type": "FeatureCollection", "features": features}

    # Separate FeatureCollections for start and end Point markers, so the
    # dashboard can render them as different-colored layers via ha-map-card.
    starts: list[dict] = []
    ends: list[dict] = []
    for j in journeys:
        wps = j.get("waypoints") or []
        if len(wps) < 2:
            continue
        first = wps[0]
        last = wps[-1]
        starts.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [round(first["lon"], 6), round(first["lat"], 6)],
            },
            "properties": {"kind": "start"},
        })
        ends.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [round(last["lon"], 6), round(last["lat"], 6)],
            },
            "properties": {"kind": "end"},
        })
    attrs["geojson_starts"] = {"type": "FeatureCollection", "features": starts}
    attrs["geojson_ends"] = {"type": "FeatureCollection", "features": ends}
    return attrs


def _journey_attrs(coord: MiHomeCoordinator, eid: int) -> dict[str, Any]:
    journeys = coord.get_journeys(eid)
    if not journeys:
        return {}
    j = journeys[-1]
    attrs: dict[str, Any] = {
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
    if j.get("start_location"):
        attrs["start_lat"] = j["start_location"].get("lat")
        attrs["start_lon"] = j["start_location"].get("lon")
    if j.get("end_location"):
        attrs["end_lat"] = j["end_location"].get("lat")
        attrs["end_lon"] = j["end_location"].get("lon")
    # GeoJSON LineString for ha-map-card and similar frontend cards.
    waypoints = j.get("waypoints") or []
    if len(waypoints) >= 2:
        coordinates = [
            [round(wp["lon"], 6), round(wp["lat"], 6)]
            for wp in waypoints
            if wp.get("lat") is not None and wp.get("lon") is not None
        ]
        if len(coordinates) >= 2:
            attrs["geojson"] = {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": coordinates,
                },
                "properties": {
                    "distance_km": j.get("distance_km"),
                    "max_speed": j.get("max_speed"),
                    "avg_speed": j.get("avg_speed"),
                },
            }
    return attrs


def _alarm_count(coord: MiHomeCoordinator, eid: int) -> int:
    return len((coord.data or {}).get("alarms", []))


def _battery_voltage(coord: MiHomeCoordinator, eid: int) -> float | None:
    return (coord.data or {}).get("battery", {}).get(eid)


SENSOR_DESCRIPTIONS: tuple[MiSensorDescription, ...] = (
    MiSensorDescription(
        key="speed",
        translation_key="speed",
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c, e: _live(c, e).get("speed"),
    ),
    MiSensorDescription(
        key="address",
        translation_key="address",
        value_fn=_format_address,
        attrs_fn=_address_attrs,
    ),
    MiSensorDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_battery_voltage,
    ),
    MiSensorDescription(
        key="last_journey_distance",
        translation_key="last_journey_distance",
        native_unit_of_measurement="km",
        value_fn=_journey_distance,
        attrs_fn=_journey_attrs,
    ),
    MiSensorDescription(
        key="last_journey_duration",
        translation_key="last_journey_duration",
        native_unit_of_measurement="min",
        value_fn=_journey_duration,
    ),
    MiSensorDescription(
        key="last_journey_max_speed",
        translation_key="last_journey_max_speed",
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        value_fn=_journey_max_speed,
    ),
    MiSensorDescription(
        key="last_journey_avg_speed",
        translation_key="last_journey_avg_speed",
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        value_fn=_journey_avg_speed,
    ),
    MiSensorDescription(
        key="journeys_for_date",
        translation_key="journeys_for_date",
        value_fn=_journeys_for_date_count,
        attrs_fn=_journeys_for_date_attrs,
    ),
    MiSensorDescription(
        key="alarm_count",
        translation_key="alarm_count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_alarm_count,
    ),
)


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
        for desc in SENSOR_DESCRIPTIONS:
            entities.append(MiSensor(coordinator, entry, eid, info, desc))
    async_add_entities(entities)


class MiSensor(CoordinatorEntity[MiHomeCoordinator], SensorEntity):
    """Generic MI sensor driven by an entity description + value_fn."""

    _attr_has_entity_name = True
    entity_description: MiSensorDescription

    def __init__(
        self,
        coordinator: MiHomeCoordinator,
        entry: ConfigEntry,
        entity_id: int,
        info: dict,
        description: MiSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._mi_entity_id = entity_id
        licence = info.get("license", "unknown").replace("-", "").lower()
        self._attr_unique_id = f"{entry.entry_id}_{licence}_{description.key}"
        self._attr_device_info = build_device_info(entity_id, info)

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator, self._mi_entity_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.entity_description.attrs_fn is None:
            return {}
        return self.entity_description.attrs_fn(self.coordinator, self._mi_entity_id)
