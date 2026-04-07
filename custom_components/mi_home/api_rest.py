"""Moving Intelligence official REST API client.

Uses api-app.movingintelligence.com with HMAC SHA-512 signed requests.

Auth headers:
    X-Mi-User       — account email
    X-Mi-Nonce      — random 8+ char string (cannot repeat within 10 min)
    X-Mi-Timestamp  — current epoch seconds (must be within 5 min of server)
    X-Signature     — "sha512 " + hex(sha512(path + user + nonce + ts + secret))
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import time
from typing import Any

import aiohttp

from .const import (
    REST_API_BASE,
    REST_API_DETAILED_TRIPS,
    REST_API_OBJECTS,
    REST_API_ODOMETER,
    REST_API_PERSONS,
    REST_API_TRIP_CLASSIFICATIONS,
    REST_API_TRIP_PERIODS,
)

_LOGGER = logging.getLogger(__name__)


class MiRestError(Exception):
    """Raised when a REST API call fails."""


class MiRestForbidden(MiRestError):
    """Raised when the API returns 403 (likely missing permissions)."""


class MiRestClient:
    """Official Moving Intelligence REST API client.

    Authentication is via HMAC SHA-512 signature using a shared API secret.
    Free tier endpoints: objects, persons, classifications, periods.
    Paid tier endpoints: detailed trips, odometer (will raise MiRestForbidden
    if account doesn't have the rights).
    """

    def __init__(
        self,
        user: str,
        api_secret: str,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._user = user
        self._secret = api_secret
        self._external_session = session is not None
        self._session = session
        self._base = REST_API_BASE

    async def __aenter__(self) -> MiRestClient:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *_: Any) -> None:
        if not self._external_session and self._session:
            await self._session.close()
            self._session = None

    # -- Free tier --

    async def get_objects(self) -> list[dict]:
        """Fetch all objects (vehicles) on the account."""
        result = await self._get(REST_API_OBJECTS)
        return result if isinstance(result, list) else []

    async def get_persons(self) -> list[dict]:
        """Fetch all persons on the account."""
        result = await self._get(REST_API_PERSONS)
        return result if isinstance(result, list) else []

    async def get_trip_classifications(self) -> list[str]:
        """Fetch valid trip classification values."""
        result = await self._get(REST_API_TRIP_CLASSIFICATIONS)
        return result if isinstance(result, list) else []

    async def get_trip_periods(self) -> list[str]:
        """Fetch valid trip period values."""
        result = await self._get(REST_API_TRIP_PERIODS)
        return result if isinstance(result, list) else []

    # -- Paid tier (return None on 403 instead of raising) --

    async def get_detailed_trips(
        self,
        object_id: int,
        period: str | None = None,
        classifications: list[str] | None = None,
        start_date: int | None = None,
        end_date: int | None = None,
    ) -> list[dict] | None:
        """Fetch detailed trips for an object (with location & speed waypoints).

        Returns None if the account doesn't have permissions for this endpoint.
        """
        path = REST_API_DETAILED_TRIPS.format(object_id=object_id)
        params: dict[str, Any] = {}
        if classifications:
            params["classifications"] = ",".join(classifications)
        if period:
            params["period"] = period
        if start_date is not None:
            params["startDate"] = start_date
        if end_date is not None:
            params["endDate"] = end_date
        try:
            result = await self._get(path, params=params)
            return result if isinstance(result, list) else None
        except MiRestForbidden:
            _LOGGER.debug(
                "Detailed trips endpoint forbidden for object %s "
                "(likely needs paid permissions)", object_id,
            )
            return None

    async def get_odometer(self, object_id: int) -> dict | None:
        """Fetch the latest known odometer value for an object.

        Returns None if the account doesn't have permissions for this endpoint.
        """
        path = REST_API_ODOMETER.format(object_id=object_id)
        try:
            result = await self._get(path)
            return result if isinstance(result, dict) else None
        except MiRestForbidden:
            _LOGGER.debug(
                "Odometer endpoint forbidden for object %s "
                "(likely needs paid permissions)", object_id,
            )
            return None

    # -- Internal --

    def _sign(self, path_with_query: str) -> dict[str, str]:
        """Generate signed auth headers for a given URL path+query."""
        nonce = secrets.token_hex(8)  # 16 hex chars (8+ required)
        timestamp = str(int(time.time()))
        to_hash = path_with_query + self._user + nonce + timestamp + self._secret
        signature = hashlib.sha512(to_hash.encode()).hexdigest()
        return {
            "X-Mi-User": self._user,
            "X-Mi-Nonce": nonce,
            "X-Mi-Timestamp": timestamp,
            "X-Signature": f"sha512 {signature}",
        }

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Make a signed GET request."""
        assert self._session is not None, "Client not initialized — use async with"
        url = self._base + path
        # Build the full request to capture the encoded path+query for signing
        req = aiohttp.client.URL(url)
        if params:
            req = req.with_query(params)
        sign_path = str(req)[len(self._base):]
        headers = self._sign(sign_path)
        try:
            async with self._session.get(str(req), headers=headers) as resp:
                if resp.status == 403:
                    raise MiRestForbidden(f"403 Forbidden on {path}")
                if resp.status != 200:
                    text = await resp.text()
                    raise MiRestError(
                        f"REST API error {resp.status} on {path}: {text[:200]}"
                    )
                return await resp.json(content_type=None)
        except aiohttp.ClientError as e:
            raise MiRestError(f"Connection error on {path}: {e}") from e

    async def test_credentials(self) -> bool:
        """Quick check that the API key works by calling a free endpoint."""
        try:
            await self.get_objects()
            return True
        except (MiRestError, MiRestForbidden):
            return False
