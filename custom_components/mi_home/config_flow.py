"""Config flow for Moving Intelligence integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .api import MiAuthError, MiSessionClient
from .api_rest import MiRestClient
from .const import (
    CONF_API_KEY,
    CONF_EMAIL,
    CONF_MAX_JOURNEYS,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    DEFAULT_MAX_JOURNEYS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_API_KEY): str,
    }
)


class MiHomeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow: email/password + optional API key."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1: credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL]
            password = user_input[CONF_PASSWORD]
            api_key = user_input.get(CONF_API_KEY, "")

            # Prevent duplicate entries for same email
            await self.async_set_unique_id(email.lower())
            self._abort_if_unique_id_configured()

            # Validate session credentials by attempting login
            try:
                async with MiSessionClient() as client:
                    await client.login(email, password)
                    context = await client.get_context()

                # Check we got at least one entity (vehicle)
                entities = [
                    r for r in context.get("rights", [])
                    if "entityPropertiesDTO" in r
                ]
                if not entities:
                    errors["base"] = "no_vehicles"
                else:
                    # If an API key was provided, validate it but don't fail
                    # the whole config if it's invalid — log a warning instead
                    if api_key:
                        try:
                            async with MiRestClient(email, api_key) as rest:
                                if not await rest.test_credentials():
                                    _LOGGER.warning(
                                        "API key provided but validation failed; "
                                        "session-only mode will be used"
                                    )
                                    api_key = ""
                        except Exception:
                            _LOGGER.warning(
                                "API key validation failed; session-only mode will be used"
                            )
                            api_key = ""

                    return self.async_create_entry(
                        title=email,
                        data={
                            CONF_EMAIL: email,
                            CONF_PASSWORD: password,
                            CONF_API_KEY: api_key,
                        },
                    )

            except MiAuthError:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected error during login")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> MiHomeOptionsFlow:
        """Return the options flow handler."""
        return MiHomeOptionsFlow(config_entry)


class MiHomeOptionsFlow(config_entries.OptionsFlow):
    """Options flow for scan interval and journey settings."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=self._entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): vol.All(int, vol.Range(min=30, max=300)),
                    vol.Optional(
                        CONF_MAX_JOURNEYS,
                        default=self._entry.options.get(
                            CONF_MAX_JOURNEYS, DEFAULT_MAX_JOURNEYS
                        ),
                    ): vol.All(int, vol.Range(min=10, max=1000)),
                }
            ),
        )
