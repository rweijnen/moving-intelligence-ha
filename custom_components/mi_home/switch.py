"""Switch platform for Moving Intelligence — immobilizer control."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import MiApiError
from .const import DOMAIN
from .coordinator import MiHomeCoordinator
from .device_tracker import build_device_info

_LOGGER = logging.getLogger(__name__)

# Immobilizer status values that mean engine is currently blocked
BLOCKED_STATUSES = {
    "STARTING_NOT_POSSIBLE",
    "BLOCKED",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up MI switch entities."""
    coordinator: MiHomeCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        MiImmobilizerSwitch(coordinator, entry, eid)
        for eid in coordinator.entity_ids
    ]
    async_add_entities(entities)


class MiImmobilizerSwitch(CoordinatorEntity[MiHomeCoordinator], SwitchEntity):
    """Switch to manually block/unblock the engine immobilizer.

    on  = engine is blocked (cannot start)
    off = engine is not blocked (can start)
    """

    _attr_has_entity_name = True
    _attr_translation_key = "immobilizer"
    _attr_device_class = SwitchDeviceClass.SWITCH

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
        self._attr_unique_id = f"{entry.entry_id}_{licence}_immobilizer"
        self._attr_device_info = build_device_info(entity_id, info)

    @property
    def _miblock(self) -> dict:
        data = self.coordinator.data or {}
        return data.get("miblock", {}).get(self._mi_entity_id, {}) or {}

    @property
    def is_on(self) -> bool | None:
        """Return True when engine is currently blocked."""
        status = self._miblock.get("immobilizerStatus")
        if status is None:
            return None
        return status in BLOCKED_STATUSES

    @property
    def available(self) -> bool:
        """Available only when we have a status from the API."""
        return self._miblock.get("immobilizerStatus") is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        m = self._miblock
        attrs = {}
        for key in (
            "immobilizerStatus",
            "immobilizerMode",
            "manualBlockAllowed",
            "manualUnblockAllowed",
            "jammed",
            "calendarUsed",
        ):
            if key in m:
                attrs[key] = m[key]
        return attrs

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Block the engine."""
        if not self._miblock.get("manualBlockAllowed", False):
            raise HomeAssistantError(
                "Manual immobilizer block is not allowed for this vehicle"
            )
        try:
            await self.coordinator.client.miblock_block(self._mi_entity_id)
        except MiApiError as e:
            raise HomeAssistantError(f"Failed to block immobilizer: {e}") from e
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Unblock the engine."""
        if not self._miblock.get("manualUnblockAllowed", False):
            raise HomeAssistantError(
                "Manual immobilizer unblock is not allowed for this vehicle"
            )
        try:
            await self.coordinator.client.miblock_unblock(self._mi_entity_id)
        except MiApiError as e:
            raise HomeAssistantError(f"Failed to unblock immobilizer: {e}") from e
        await self.coordinator.async_request_refresh()
