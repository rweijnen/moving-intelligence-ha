"""DataUpdateCoordinator for Moving Intelligence.

Architecture:
- A slow REST poll (every COORD_FALLBACK_INTERVAL seconds, default 5 min)
  fetches battery voltage, immobilizer status, alarm messages, and a fresh
  live snapshot per entity. This is the fallback path.
- A persistent STOMP-over-WebSocket connection subscribes to
  /user/topic/positionEvent and pushes live position updates into the
  coordinator state via async_set_updated_data().
- Journey detection runs on every live update (push or poll) and persists
  completed journeys to the HA Store.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from math import atan2, cos, radians, sin, sqrt
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MiApiError, MiAuthError, MiSessionClient, _scale_coordinates
from .const import (
    CONF_EMAIL,
    CONF_MAX_JOURNEYS,
    CONF_PASSWORD,
    COORD_FALLBACK_INTERVAL,
    DEFAULT_MAX_JOURNEYS,
    DOMAIN,
    STOMP_TOPIC_LIVE_ROUTE,
    STOMP_TOPIC_POSITION_EVENT,
)
from .stomp import MiStompClient

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in km between two lat/lon points."""
    r = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = (
        sin(dlat / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    )
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
        "avg_speed": round(sum(moving_speeds) / len(moving_speeds))
        if moving_speeds
        else 0,
    }


class MiHomeCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that polls MI live API and streams updates over STOMP."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(seconds=COORD_FALLBACK_INTERVAL),
        )
        self._store = Store(hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._client = MiSessionClient()
        self._stomp: MiStompClient | None = None
        self._store_loaded = False
        self._session_cookies: dict[str, str] = {}
        self._entity_ids: list[int] = []
        self._entities_info: dict[int, dict] = {}
        self._last_route_start: dict[int, int | None] = {}
        self._last_engine_on: dict[int, bool | None] = {}
        self._last_route_points: dict[int, list] = {}
        self._journeys: dict[str, list[dict]] = {}
        self._battery_data: dict[int, float | None] = {}
        self._miblock_data: dict[int, dict] = {}
        self._alarm_messages: list = []
        self._live_data: dict[int, dict] = {}
        self._last_save_time: float = 0.0
        self._selected_dates: dict[int, date] = {}  # entity_id → user-selected date

    @property
    def client(self) -> MiSessionClient:
        return self._client

    @property
    def entity_ids(self) -> list[int]:
        return self._entity_ids

    @property
    def entities_info(self) -> dict[int, dict]:
        return self._entities_info

    def get_journeys(self, entity_id: int) -> list[dict]:
        return self._journeys.get(str(entity_id), [])

    def get_selected_date(self, entity_id: int) -> date:
        """Return the user-selected date for journey filtering.

        Defaults to the date of the most recent stored journey, or today if
        none exist. This makes the map "just work" right after install: the
        user doesn't have to know which day to pick to see their last trip.
        """
        if entity_id in self._selected_dates:
            return self._selected_dates[entity_id]
        journeys = self.get_journeys(entity_id)
        if journeys:
            last_ts = journeys[-1].get("start_time")
            if last_ts:
                return datetime.fromtimestamp(last_ts, tz=timezone.utc).date()
        return date.today()

    def set_selected_date(self, entity_id: int, value: date) -> None:
        """Update the selected date and notify listeners (for date-filtered sensor)."""
        self._selected_dates[entity_id] = value
        self.async_update_listeners()
        self.hass.async_create_background_task(
            self._maybe_save_store(force=True),
            name=f"{DOMAIN}_save_selected_date",
        )

    def get_journeys_on_date(self, entity_id: int, target: date) -> list[dict]:
        """Return all stored journeys whose start time falls on the given date."""
        result = []
        for j in self.get_journeys(entity_id):
            ts = j.get("start_time")
            if not ts:
                continue
            if datetime.fromtimestamp(ts, tz=timezone.utc).date() == target:
                result.append(j)
        return result

    # -- Setup / shutdown --

    async def _async_setup(self) -> None:
        """Initialize the API client and load persistent state.

        Called once by HA before the first refresh.
        """
        await self._client.connect()
        await self._load_store()
        self._store_loaded = True

    async def async_shutdown(self) -> None:
        """Stop STOMP and close the API client."""
        await super().async_shutdown()
        if self._stomp:
            await self._stomp.stop()
            self._stomp = None
        await self._client.close()

    # -- Session management --

    async def _ensure_session(self) -> None:
        """Ensure we have a valid session, re-login if needed.

        Raises ConfigEntryAuthFailed on credential failure → triggers reauth.
        """
        if self._session_cookies:
            self._client.load_cookies(self._session_cookies)

        if await self._client.is_logged_in():
            return

        _LOGGER.debug("Session expired, re-logging in")
        email = self.config_entry.data[CONF_EMAIL]
        password = self.config_entry.data[CONF_PASSWORD]
        try:
            await self._client.login(email, password)
        except MiAuthError as e:
            raise ConfigEntryAuthFailed(f"Login failed: {e}") from e
        self._session_cookies = self._client.export_cookies()
        await self._maybe_save_store(force=True)

        # Update STOMP token if needed
        if self._stomp:
            new_token = self._client.get_session_id()
            if new_token:
                self._stomp.update_session_id(new_token)

    # -- STOMP push handling --

    async def _start_stomp(self) -> None:
        """Start the STOMP client and subscribe to live topics."""
        token = self._client.get_session_id()
        if not token:
            _LOGGER.warning("No JSESSIONID available — cannot start STOMP push")
            return
        if self._stomp is not None:
            return
        self._stomp = MiStompClient(
            session_id=token,
            on_message=self._on_stomp_message,
            on_state_change=self._on_stomp_state,
        )
        await self._stomp.start()
        await self._stomp.subscribe(STOMP_TOPIC_POSITION_EVENT)
        await self._stomp.subscribe(STOMP_TOPIC_LIVE_ROUTE)

    def _on_stomp_message(self, destination: str, body: dict) -> None:
        """Handle a STOMP MESSAGE frame from the live topics."""
        if destination not in (STOMP_TOPIC_POSITION_EVENT, STOMP_TOPIC_LIVE_ROUTE):
            return
        entity_id = body.get("entityId")
        if not isinstance(entity_id, int):
            return
        # Scale coordinates from microdegrees
        _scale_coordinates(body)
        # Merge into existing live state (preserve any keys not in the push)
        current = self._live_data.get(entity_id, {})
        merged = {**current, **body}
        self._live_data[entity_id] = merged
        # Detect journey transitions
        self._detect_journey(entity_id, merged)
        # Push update to entities (no I/O, no await needed)
        self.async_set_updated_data(self._build_data())

    def _on_stomp_state(self, connected: bool) -> None:
        if connected:
            _LOGGER.info("STOMP push connected — live updates active")
        else:
            _LOGGER.debug("STOMP push disconnected — falling back to polling")

    # -- Polling (fallback + slow data) --

    async def _async_update_data(self) -> dict[str, Any]:
        """Slow REST poll: refresh battery, miblock, alarms, and live snapshot."""
        try:
            await self._ensure_session()
        except ConfigEntryAuthFailed:
            raise
        except MiApiError as e:
            raise UpdateFailed(f"Session error: {e}") from e

        # First successful run: fetch context to discover entities
        if not self._entity_ids:
            try:
                context = await self._client.get_context()
                self._parse_context(context)
            except MiAuthError as e:
                raise ConfigEntryAuthFailed(str(e)) from e
            except MiApiError as e:
                raise UpdateFailed(f"Failed to get context: {e}") from e

            # Start STOMP push now that we know who to listen for
            await self._start_stomp()

        # Refresh slow-changing data: live snapshot (in case STOMP missed
        # something), battery, miblock, alarms.
        for eid in self._entity_ids:
            try:
                live = await self._client.get_live(eid)
                self._live_data[eid] = live
                self._detect_journey(eid, live)
            except MiAuthError as e:
                raise ConfigEntryAuthFailed(str(e)) from e
            except MiApiError as e:
                _LOGGER.warning("Live fetch failed for %s: %s", eid, e)

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

        # Persist any cookie rotation
        new_cookies = self._client.export_cookies()
        if new_cookies != self._session_cookies:
            self._session_cookies = new_cookies
            await self._maybe_save_store()

        return self._build_data()

    def _build_data(self) -> dict[str, Any]:
        """Build the data dict that gets pushed to entities."""
        return {
            "live": dict(self._live_data),
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

    # -- Journey detection --

    def _detect_journey(self, entity_id: int, live: dict) -> None:
        """Detect journey boundaries and record completed journeys."""
        route_start = live.get("routeStartDate")
        engine_on = live.get("engineOn")
        route_points = live.get("routePoints", [])

        prev_start = self._last_route_start.get(entity_id)
        prev_engine = self._last_engine_on.get(entity_id)
        prev_points = self._last_route_points.get(entity_id, [])
        first_observation = (
            entity_id not in self._last_route_start
            and entity_id not in self._last_engine_on
        )

        # Update tracking state
        self._last_route_start[entity_id] = route_start
        self._last_engine_on[entity_id] = engine_on
        if route_points:
            self._last_route_points[entity_id] = route_points

        # First observation after install / restart:
        # If we have routePoints from a journey that already ended (engine off),
        # record them so the user sees their most recent journey immediately.
        # If engine is currently on, the journey is in progress — don't record
        # yet, just remember state.
        if first_observation:
            if route_points and engine_on is False:
                self._record_journey(entity_id, route_points)
            return

        journey_ended = False
        points_to_save = prev_points

        if route_start != prev_start and route_start is not None and prev_points:
            # New journey ID seen → previous one is done, save its last-known points
            journey_ended = True
        elif prev_engine is True and engine_on is False:
            # Engine just turned off → use the most complete point set we have
            journey_ended = True
            points_to_save = route_points if route_points else prev_points

        if journey_ended and points_to_save:
            self._record_journey(entity_id, points_to_save)

    def _record_journey(self, entity_id: int, route_points: list) -> None:
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
        # Skip duplicate if start_time already recorded
        if any(j.get("start_time") == journey["start_time"] for j in self._journeys[key]):
            return
        self._journeys[key].append(journey)

        # FIFO eviction
        max_journeys = self.config_entry.options.get(
            CONF_MAX_JOURNEYS, DEFAULT_MAX_JOURNEYS
        )
        while len(self._journeys[key]) > max_journeys:
            self._journeys[key].pop(0)

        # Fire HA event for automations
        self.hass.bus.async_fire(
            f"{DOMAIN}_journey_completed",
            {
                "mi_entity_id": entity_id,
                "distance_km": stats["distance_km"],
                "max_speed": stats["max_speed"],
                "avg_speed": stats["avg_speed"],
                "duration_min": round(
                    (journey["end_time"] - journey["start_time"]) / 60
                ),
                "waypoint_count": len(waypoints),
            },
        )

        _LOGGER.info(
            "Journey recorded for entity %s: %.1f km, %d waypoints",
            entity_id, stats["distance_km"], len(waypoints),
        )

        self.hass.async_create_background_task(
            self._maybe_save_store(force=True),
            name=f"{DOMAIN}_save_journey",
        )

    # -- Persistent storage --

    async def _load_store(self) -> None:
        """Load persistent data from Store."""
        stored = await self._store.async_load() or {}
        self._journeys = stored.get("journeys", {})
        self._session_cookies = stored.get("session_cookie", {})
        self._last_route_start = {
            int(k): v for k, v in stored.get("last_route_start", {}).items()
        }
        # Selected dates: stored as ISO strings → convert back to date objects
        self._selected_dates = {}
        for k, v in stored.get("selected_dates", {}).items():
            try:
                self._selected_dates[int(k)] = date.fromisoformat(v)
            except (ValueError, TypeError):
                pass

    async def _maybe_save_store(self, force: bool = False) -> None:
        """Persist data to Store, throttled to once per minute unless forced."""
        now = time.monotonic()
        if not force and (now - self._last_save_time) < 60:
            return
        self._last_save_time = now
        await self._store.async_save({
            "journeys": self._journeys,
            "session_cookie": self._session_cookies,
            "last_route_start": {
                str(k): v for k, v in self._last_route_start.items()
            },
            "selected_dates": {
                str(k): v.isoformat() for k, v in self._selected_dates.items()
            },
        })
