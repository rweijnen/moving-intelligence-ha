"""Moving Intelligence session-based API client.

Uses the app.movingintelligence.com endpoints with JSESSIONID cookie auth.
All endpoints are POST with JSON content type.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
from yarl import URL

from .const import (
    CLIENT_OS_VERSION,
    CLIENT_PLATFORM,
    CLIENT_VERSION,
    COORD_SCALE,
    REQUEST_TIMEOUT,
    SESSION_API_ALARM_BLOCK_GET,
    SESSION_API_ALARM_BLOCK_SET,
    SESSION_API_ALARM_BLOCK_UNSET,
    SESSION_API_ALARM_MESSAGES,
    SESSION_API_BASE,
    SESSION_API_BATTERY,
    SESSION_API_GET_CONTEXT,
    SESSION_API_IS_LOGGED_IN,
    SESSION_API_LIVE,
    SESSION_API_LOGIN,
    SESSION_API_LOGOUT,
    SESSION_API_MIBLOCK_BLOCK,
    SESSION_API_MIBLOCK_GET,
    SESSION_API_MIBLOCK_UNBLOCK,
)

_LOGGER = logging.getLogger(__name__)


class MiAuthError(Exception):
    """Raised when authentication fails."""


class MiApiError(Exception):
    """Raised when an API call fails."""


class MiSessionClient:
    """Session-based API client for Moving Intelligence."""

    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        self._external_session = session is not None
        self._session: aiohttp.ClientSession | None = session
        self._base = SESSION_API_BASE
        self._timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)

    @property
    def is_connected(self) -> bool:
        """Return True if the underlying HTTP session is open."""
        return self._session is not None and not self._session.closed

    async def connect(self) -> None:
        """Open the underlying HTTP session if not already open."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                cookie_jar=aiohttp.CookieJar(unsafe=True),
                timeout=self._timeout,
            )
            self._external_session = False

    async def close(self) -> None:
        """Close the underlying HTTP session if we own it."""
        if not self._external_session and self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def __aenter__(self) -> MiSessionClient:
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # -- Session management --

    async def login(self, email: str, password: str) -> bool:
        """Login and obtain JSESSIONID cookie.

        Returns True on success, raises MiAuthError on failure.
        """
        data = {
            "email": email,
            "password": password,
            "client": CLIENT_PLATFORM,
            "version": CLIENT_VERSION,
            "osVersion": CLIENT_OS_VERSION,
        }
        try:
            resp_data = await self._post(SESSION_API_LOGIN, data)
        except MiApiError as e:
            raise MiAuthError(f"Login failed: {e}") from e
        if resp_data is None:
            raise MiAuthError("Login returned empty response")
        return True

    async def is_logged_in(self) -> bool:
        """Check if current session is still valid."""
        try:
            resp = await self._post(SESSION_API_IS_LOGGED_IN, {})
            return isinstance(resp, dict) and resp.get("value") is True
        except (MiApiError, MiAuthError):
            return False

    async def logout(self) -> None:
        """End the current session."""
        try:
            await self._post(SESSION_API_LOGOUT, {})
        except (MiApiError, MiAuthError):
            pass

    def export_cookies(self) -> dict[str, str]:
        """Export session cookies for persistent storage."""
        if not self._session or not self._session.cookie_jar:
            return {}
        cookies: dict[str, str] = {}
        for cookie in self._session.cookie_jar:
            if cookie.key == "JSESSIONID":
                cookies[cookie.key] = cookie.value
        return cookies

    def load_cookies(self, cookies: dict[str, str]) -> None:
        """Restore session cookies from persistent storage."""
        if not self._session or not cookies:
            return
        self._session.cookie_jar.update_cookies(cookies, response_url=URL(self._base))

    def get_session_id(self) -> str | None:
        """Return the current JSESSIONID value, or None if not logged in."""
        return self.export_cookies().get("JSESSIONID")

    # -- Data endpoints --

    async def get_context(self) -> dict:
        """Fetch account context: entities, persons, rights, services."""
        result = await self._post(SESSION_API_GET_CONTEXT, True)
        if not isinstance(result, dict):
            raise MiApiError("get-context returned unexpected data")
        return result

    async def get_live(self, entity_id: int) -> dict:
        """Fetch live data for an entity: position, speed, engine, route points.

        Coordinates are returned converted from microdegrees to degrees.
        """
        result = await self._post(SESSION_API_LIVE, entity_id)
        if not isinstance(result, dict):
            raise MiApiError(f"get_live({entity_id}) returned unexpected data")
        _scale_coordinates(result)
        return result

    async def get_miblock_status(self, entity_id: int) -> dict:
        """Fetch immobilizer status."""
        result = await self._post(f"{SESSION_API_MIBLOCK_GET}/{entity_id}", None)
        if not isinstance(result, dict):
            raise MiApiError(f"miblock_get({entity_id}) returned unexpected data")
        return result

    async def miblock_block(self, entity_id: int) -> None:
        """Block the engine (activate immobilizer)."""
        await self._post(f"{SESSION_API_MIBLOCK_BLOCK}/{entity_id}", None)

    async def miblock_unblock(self, entity_id: int) -> None:
        """Unblock the engine (deactivate immobilizer)."""
        await self._post(f"{SESSION_API_MIBLOCK_UNBLOCK}/{entity_id}", None)

    async def get_alarm_block(self, entity_id: int) -> dict | None:
        """Fetch alarm block status for an entity."""
        return await self._post(f"{SESSION_API_ALARM_BLOCK_GET}/{entity_id}", {})

    async def set_alarm_block_period(self, entity_id: int, period: dict) -> None:
        """Set alarm block period."""
        await self._post(f"{SESSION_API_ALARM_BLOCK_SET}/{entity_id}", period)

    async def unset_alarm_block(self, entity_id: int) -> None:
        """Remove alarm block."""
        await self._post(f"{SESSION_API_ALARM_BLOCK_UNSET}/{entity_id}", {})

    async def get_alarm_messages(self) -> list:
        """Fetch alarm messages."""
        result = await self._post(SESSION_API_ALARM_MESSAGES, None)
        return result if isinstance(result, list) else []

    async def get_battery_voltage(self, entity_id: int) -> float | None:
        """Fetch battery voltage for an entity."""
        result = await self._post(f"{SESSION_API_BATTERY}/{entity_id}", {})
        if isinstance(result, (int, float)):
            return float(result)
        return None

    # -- Internal --

    async def _post(self, endpoint: str, data: Any = None) -> Any:
        """Make an authenticated POST request."""
        if self._session is None or self._session.closed:
            raise RuntimeError("MiSessionClient not connected — call connect() first")
        url = f"{self._base}/{endpoint}"
        try:
            async with self._session.post(
                url,
                json=data,
                headers={"Content-Type": "application/json"},
            ) as resp:
                if resp.status in (401, 403):
                    raise MiAuthError(f"Session expired or forbidden: {endpoint}")
                if resp.status != 200:
                    text = await resp.text()
                    raise MiApiError(
                        f"API error {resp.status} on {endpoint}: {text[:200]}"
                    )
                return await resp.json(content_type=None)
        except aiohttp.ClientError as e:
            raise MiApiError(f"Connection error on {endpoint}: {e}") from e


def _scale_coordinates(live_data: dict) -> None:
    """Convert microdegree integer coordinates to float degrees, in place."""
    for key in ("latitude", "longitude"):
        val = live_data.get(key)
        if isinstance(val, (int, float)):
            live_data[key] = val / COORD_SCALE
    points = live_data.get("routePoints")
    if isinstance(points, list):
        for point in points:
            for key in ("latitude", "longitude"):
                val = point.get(key)
                if isinstance(val, (int, float)):
                    point[key] = val / COORD_SCALE
