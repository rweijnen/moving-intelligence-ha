"""DataUpdateCoordinator for Moving Intelligence."""
from __future__ import annotations

import logging
import time
from datetime import timedelta
from math import atan2, cos, radians, sin, sqrt
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MiApiError, MiAuthError, MiSessionClient
from .const import (
    CONF_EMAIL,
    CONF_MAX_JOURNEYS,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    COORD_BATTERY_INTERVAL,
    DEFAULT_MAX_JOURNEYS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in km between two lat/lon points."""
    r = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return r * 2 * atan2(sqrt(a), sqrt(1 - a))


def _calculate_journey_stats(waypoints: list[dict]) -> dict:
    """Calculate distance, max_speed, avg_speed from waypoints."""
    if not waypoints:
        return {"distance_km": 0.0, "max_speed": 0, "avg_speed": 0}

    total_dist = 0.0
    speeds = [wp["speed"] for wp in waypoints if wp.get("speed") is not None]
    for i in range(1, len(waypoints)):
        total_dist += _haversine_km(
            waypoints[i - 1]["lat"], waypoints[i - 1]["lon"],
            waypoints[i]["lat"], waypoints[i]["lon"],
        )

    moving_speeds = [s for s in speeds if s > 0]
    return {
        "distance_km": round(total_dist, 2),
        "max_speed": max(speeds) if speeds else 0,
        "avg_speed": round(sum(moving_speeds) / len(moving_speeds)) if moving_speeds else 0,
    }


class MiHomeCoordinator(DataUpdateCoordinator):
    """Coordinator that polls MI live API and detects journeys."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
        )
        self._entry = entry
        self._store = Store(hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._client = MiSessionClient()
        self._store_loaded = False
        self._session_cookies: dict[str, str] = {}
        self._entity_ids: list[int] = []
        self._entities_info: dict[int, dict] = {}  # entity_id → entityPropertiesDTO
        self._last_route_start: dict[int, int | None] = {}  # entity_id → routeStartDate
        self._last_engine_on: dict[int, bool | None] = {}
        self._last_route_points: dict[int, list] = {}
        self._journeys: dict[str, list[dict]] = {}  # str(entity_id) → journey list
        self._last_battery_poll: float = 0.0
        self._battery_data: dict[int, float | None] = {}
        self._miblock_data: dict[int, dict] = {}
        self._alarm_messages: list = []
        self._context: dict = {}

    @property
    def client(self) -> MiSessionClient:
        """Return the API client."""
        return self._client

    @property
    def entity_ids(self) -> list[int]:
        """Return tracked entity IDs."""
        return self._entity_ids

    @property
    def entities_info(self) -> dict[int, dict]:
        """Return entity properties keyed by ID."""
        return self._entities_info

    def get_journeys(self, entity_id: int) -> list[dict]:
        """Return stored journeys for an entity."""
        return self._journeys.get(str(entity_id), [])

    async def _async_setup(self) -> None:
        """Initialize the client session."""
        await self._client.__aenter__()

    async def async_shutdown(self) -> None:
        """Clean up the client session."""
        await self._client.__aexit__(None, None, None)

    async def _ensure_session(self) -> None:
        """Ensure we have a valid session, re-login if needed."""
        # Try restoring cookies first
        if self._session_cookies:
            self._client.load_cookies(self._session_cookies)

        if await self._client.is_logged_in():
            return

        _LOGGER.debug("Session expired, re-logging in")
        email = self._entry.data[CONF_EMAIL]
        password = self._entry.data[CONF_PASSWORD]
        try:
            await self._client.login(email, password)
            self._session_cookies = self._client.export_cookies()
            await self._save_store()
        except MiAuthError as e:
            raise UpdateFailed(f"Login failed: {e}") from e

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch all data from MI."""
        # Load persistent store on first run
        if not self._store_loaded:
            await self._load_store()
            self._store_loaded = True

        # Initialize client session if needed
        if self._client._session is None:
            await self._async_setup()

        try:
            await self._ensure_session()
        except UpdateFailed:
            raise
        except Exception as e:
            raise UpdateFailed(f"Session error: {e}") from e

        # Fetch context if we don't have entities yet
        if not self._entity_ids:
            try:
                self._context = await self._client.get_context()
                self._parse_context(self._context)
            except MiApiError as e:
                raise UpdateFailed(f"Failed to get context: {e}") from e

        # Fetch live data per entity
        live_data: dict[int, dict] = {}
        for eid in self._entity_ids:
            try:
                live = await self._client.get_live(eid)
                live_data[eid] = live
                self._detect_journey(eid, live)
            except MiApiError as e:
                _LOGGER.warning("Failed to get live data for entity %s: %s", eid, e)

        # Periodic: battery, miblock, alarms (every COORD_BATTERY_INTERVAL)
        now = time.monotonic()
        if now - self._last_battery_poll >= COORD_BATTERY_INTERVAL:
            self._last_battery_poll = now
            for eid in self._entity_ids:
                try:
                    self._battery_data[eid] = await self._client.get_battery_voltage(eid)
                except MiApiError as e:
                    _LOGGER.debug("Battery fetch failed for %s: %s", eid, e)
                try:
                    self._miblock_data[eid] = await self._client.get_miblock_status(eid)
                except MiApiError as e:
                    _LOGGER.debug("Miblock fetch failed for %s: %s", eid, e)
            try:
                self._alarm_messages = await self._client.get_alarm_messages()
            except MiApiError as e:
                _LOGGER.debug("Alarm messages fetch failed: %s", e)

        # Save cookies periodically
        new_cookies = self._client.export_cookies()
        if new_cookies != self._session_cookies:
            self._session_cookies = new_cookies
            await self._save_store()

        return {
            "live": live_data,
            "battery": dict(self._battery_data),
            "miblock": dict(self._miblock_data),
            "alarms": list(self._alarm_messages),
            "entities_info": dict(self._entities_info),
        }

    def _parse_context(self, context: dict) -> None:
        """Extract entity IDs and info from account context."""
        self._entity_ids = []
        self._entities_info = {}
        for right in context.get("rights", []):
            props = right.get("entityPropertiesDTO")
            if props and props.get("id"):
                eid = props["id"]
                self._entity_ids.append(eid)
                self._entities_info[eid] = props

    def _detect_journey(self, entity_id: int, live: dict) -> None:
        """Detect journey boundaries and record completed journeys."""
        route_start = live.get("routeStartDate")
        engine_on = live.get("engineOn")
        route_points = live.get("routePoints", [])

        prev_start = self._last_route_start.get(entity_id)
        prev_engine = self._last_engine_on.get(entity_id)
        prev_points = self._last_route_points.get(entity_id, [])

        # Update tracking state
        self._last_route_start[entity_id] = route_start
        self._last_engine_on[entity_id] = engine_on
        self._last_route_points[entity_id] = route_points

        # Skip first poll — just record initial state
        if prev_start is None:
            return

        # Journey completed: routeStartDate changed (new journey started, old one is done)
        # OR engine turned off (end of journey)
        journey_ended = False
        points_to_save = prev_points

        if route_start != prev_start and prev_points:
            # New journey started → previous journey is complete
            journey_ended = True
        elif prev_engine is True and engine_on is False and prev_points:
            # Engine turned off → journey ended
            journey_ended = True
            points_to_save = route_points  # use current points (they're the full journey)

        if journey_ended and points_to_save:
            self._record_journey(entity_id, live, points_to_save)

    def _record_journey(self, entity_id: int, live: dict, route_points: list) -> None:
        """Record a completed journey to the store."""
        waypoints = [
            {
                "lat": p["latitude"],
                "lon": p["longitude"],
                "speed": p.get("speed", 0),
                "dir": p.get("direction", 0),
                "time": p.get("date", 0),
            }
            for p in route_points
            if "latitude" in p and "longitude" in p
        ]

        if len(waypoints) < 2:
            return

        stats = _calculate_journey_stats(waypoints)
        journey = {
            "start_time": waypoints[0]["time"],
            "end_time": waypoints[-1]["time"],
            "start_location": {
                "lat": waypoints[0]["lat"],
                "lon": waypoints[0]["lon"],
            },
            "end_location": {
                "lat": waypoints[-1]["lat"],
                "lon": waypoints[-1]["lon"],
            },
            **stats,
            "waypoint_count": len(waypoints),
            "waypoints": waypoints,
        }

        key = str(entity_id)
        if key not in self._journeys:
            self._journeys[key] = []

        self._journeys[key].append(journey)

        # FIFO eviction
        max_journeys = self._entry.options.get(CONF_MAX_JOURNEYS, DEFAULT_MAX_JOURNEYS)
        while len(self._journeys[key]) > max_journeys:
            self._journeys[key].pop(0)

        # Fire event
        self.hass.bus.async_fire(
            f"{DOMAIN}_journey_completed",
            {
                "entity_id": entity_id,
                "distance_km": stats["distance_km"],
                "max_speed": stats["max_speed"],
                "avg_speed": stats["avg_speed"],
                "duration_min": round((journey["end_time"] - journey["start_time"]) / 60),
                "waypoint_count": len(waypoints),
            },
        )

        _LOGGER.info(
            "Journey recorded for entity %s: %.1f km, %d waypoints",
            entity_id, stats["distance_km"], len(waypoints),
        )

        # Persist
        self.hass.async_create_task(self._save_store())

    async def _load_store(self) -> None:
        """Load persistent data from Store."""
        stored = await self._store.async_load() or {}
        self._journeys = stored.get("journeys", {})
        self._session_cookies = stored.get("session_cookie", {})
        self._last_route_start = {
            int(k): v for k, v in stored.get("last_route_start", {}).items()
        }

    async def _save_store(self) -> None:
        """Persist data to Store."""
        await self._store.async_save({
            "journeys": self._journeys,
            "session_cookie": self._session_cookies,
            "last_route_start": {
                str(k): v for k, v in self._last_route_start.items()
            },
        })
