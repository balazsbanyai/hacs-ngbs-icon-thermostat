"""Config flow for NGBS iCON Thermostat."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from aiohttp import ClientSession, DummyCookieJar
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .client import NgbsAuthError, NgbsClient, NgbsHomeNotFoundError
from .const import CONF_ICON_ID, CONF_MANUAL_HC_SWITCH, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_ICON_ID): str,
        vol.Optional(CONF_MANUAL_HC_SWITCH, default=False): bool,
    }
)


async def _validate_input(hass: Any, data: dict[str, Any]) -> dict[str, str]:
    """Validate credentials and Home ID; return title info."""
    async with ClientSession(cookie_jar=DummyCookieJar()) as session:
        client = NgbsClient(
            session=session,
            username=data[CONF_USERNAME],
            password=data[CONF_PASSWORD],
            icon_id=data[CONF_ICON_ID].strip(),
        )
        await client.login()
        home = await client.get_home()
    title = home.name or f"NGBS iCON ({data[CONF_ICON_ID].strip()})"
    return {"title": title}


class NgbsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for NGBS iCON."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            user_input[CONF_ICON_ID] = user_input[CONF_ICON_ID].strip()
            await self.async_set_unique_id(user_input[CONF_ICON_ID])
            self._abort_if_unique_id_configured()

            try:
                info = await _validate_input(self.hass, user_input)
            except NgbsAuthError:
                errors["base"] = "invalid_auth"
            except NgbsHomeNotFoundError:
                errors["base"] = "invalid_home"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected exception during config validation")
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> FlowResult:
        """Start reauthentication when credentials stop working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Collect updated username/password and reload the entry."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            data = {
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                CONF_ICON_ID: reauth_entry.data[CONF_ICON_ID],
                CONF_MANUAL_HC_SWITCH: reauth_entry.data.get(
                    CONF_MANUAL_HC_SWITCH, False
                ),
            }
            try:
                await _validate_input(self.hass, data)
            except NgbsAuthError:
                errors["base"] = "invalid_auth"
            except NgbsHomeNotFoundError:
                errors["base"] = "invalid_home"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected exception during reauth")
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME,
                        default=reauth_entry.data.get(CONF_USERNAME, ""),
                    ): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return NgbsOptionsFlow()


class NgbsOptionsFlow(config_entries.OptionsFlow):
    """Handle options for an existing entry."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options.get(
            CONF_MANUAL_HC_SWITCH,
            self.config_entry.data.get(CONF_MANUAL_HC_SWITCH, False),
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_MANUAL_HC_SWITCH, default=current): bool,
                }
            ),
        )
