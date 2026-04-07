"""Config flow for Moving Intelligence integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers import selector

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


def _user_schema(defaults: dict[str, str] | None = None) -> vol.Schema:
    """Build the credentials form schema."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_EMAIL, default=defaults.get(CONF_EMAIL, "")
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.EMAIL)
            ),
            vol.Required(CONF_PASSWORD): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Optional(
                CONF_API_KEY, default=defaults.get(CONF_API_KEY, "")
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
        }
    )


class MiHomeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow: email/password + optional API key."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL].strip().lower()
            password = user_input[CONF_PASSWORD]
            api_key = user_input.get(CONF_API_KEY, "").strip()

            await self.async_set_unique_id(email)
            self._abort_if_unique_id_configured()

            try:
                async with MiSessionClient() as client:
                    await client.login(email, password)
                    context = await client.get_context()

                entities = [
                    r for r in context.get("rights", [])
                    if "entityPropertiesDTO" in r
                ]
                if not entities:
                    errors["base"] = "no_vehicles"
                else:
                    if api_key:
                        if not await self._validate_api_key(email, api_key):
                            _LOGGER.warning(
                                "API key validation failed; "
                                "session-only mode will be used"
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
            data_schema=_user_schema(),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauth when session credentials are no longer valid."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Prompt the user for new credentials."""
        errors: dict[str, str] = {}
        existing_entry = self._get_reauth_entry()
        existing_email = (
            existing_entry.data.get(CONF_EMAIL, "") if existing_entry else ""
        )

        if user_input is not None:
            email = user_input[CONF_EMAIL].strip().lower()
            password = user_input[CONF_PASSWORD]
            api_key = user_input.get(CONF_API_KEY, "").strip()

            try:
                async with MiSessionClient() as client:
                    await client.login(email, password)
                if api_key and not await self._validate_api_key(email, api_key):
                    api_key = ""
            except MiAuthError:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected error during reauth")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    existing_entry,
                    data={
                        CONF_EMAIL: email,
                        CONF_PASSWORD: password,
                        CONF_API_KEY: api_key,
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_user_schema({CONF_EMAIL: existing_email}),
            errors=errors,
        )

    @staticmethod
    async def _validate_api_key(email: str, api_key: str) -> bool:
        """Test the optional REST API key. Returns True if valid."""
        try:
            async with MiRestClient(email, api_key) as rest:
                return await rest.test_credentials()
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> MiHomeOptionsFlow:
        """Return the options flow handler."""
        return MiHomeOptionsFlow()


class MiHomeOptionsFlow(config_entries.OptionsFlow):
    """Options flow for scan interval and journey settings."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): vol.All(int, vol.Range(min=30, max=300)),
                    vol.Optional(
                        CONF_MAX_JOURNEYS,
                        default=self.config_entry.options.get(
                            CONF_MAX_JOURNEYS, DEFAULT_MAX_JOURNEYS
                        ),
                    ): vol.All(int, vol.Range(min=10, max=1000)),
                }
            ),
        )
