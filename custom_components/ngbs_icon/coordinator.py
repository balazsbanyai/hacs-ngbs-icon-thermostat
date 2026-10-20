"""DataUpdateCoordinator for NGBS iCON."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from aiohttp import DummyCookieJar
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import HomeState, NgbsAuthError, NgbsClient, NgbsHomeNotFoundError
from .const import (
    CONF_ICON_ID,
    CONF_MANUAL_HC_SWITCH,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    SESSION_RENEW_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class NgbsCoordinator(DataUpdateCoordinator[HomeState]):
    """Polls the NGBS cloud and keeps the home state fresh."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        # Isolated cookie jar: we pass PHPSESSID explicitly like the web UI /
        # Homebridge plugin, and must not leak it into HA's shared session.
        self._session = async_create_clientsession(
            hass, cookie_jar=DummyCookieJar()
        )
        self.client = NgbsClient(
            session=self._session,
            username=entry.data[CONF_USERNAME],
            password=entry.data[CONF_PASSWORD],
            icon_id=entry.data[CONF_ICON_ID],
        )
        self._last_login: datetime | None = None
        self._disconnected = False
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=DEFAULT_SCAN_INTERVAL,
        )

    @property
    def manual_hc_switch(self) -> bool:
        """Whether Heat/Cool may ask the controller to change season."""
        if CONF_MANUAL_HC_SWITCH in self.config_entry.options:
            return bool(self.config_entry.options[CONF_MANUAL_HC_SWITCH])
        return bool(self.config_entry.data.get(CONF_MANUAL_HC_SWITCH, False))

    async def _async_update_data(self) -> HomeState:
        try:
            await self._maybe_renew_session()
            state = await self.client.get_home()
        except NgbsAuthError as err:
            # Session expired mid-flight — try one re-login before failing hard.
            try:
                await self.client.login()
                self._last_login = datetime.now()
                state = await self.client.get_home()
            except NgbsAuthError as auth_err:
                raise ConfigEntryAuthFailed(str(auth_err)) from auth_err
            except NgbsHomeNotFoundError as home_err:
                raise UpdateFailed(str(home_err)) from home_err
            except Exception as renew_err:  # noqa: BLE001
                raise UpdateFailed(str(err)) from renew_err
        except NgbsHomeNotFoundError as err:
            raise UpdateFailed(str(err)) from err
        except Exception as err:  # noqa: BLE001
            if not self._disconnected:
                _LOGGER.error("Lost contact with the NGBS iCON cloud: %s", err)
                self._disconnected = True
            raise UpdateFailed(str(err)) from err

        if self._disconnected:
            _LOGGER.info("Reconnected to the NGBS iCON cloud")
            self._disconnected = False

        return state

    async def _maybe_renew_session(self) -> None:
        """Hourly re-login backstop, matching the Homebridge plugin."""
        now = datetime.now()
        if self._last_login is None:
            await self.client.login()
            self._last_login = now
            return
        if now - self._last_login >= SESSION_RENEW_INTERVAL:
            try:
                await self.client.login()
                self._last_login = now
            except NgbsAuthError as err:
                # Keep the previous session if renewal fails.
                _LOGGER.warning("Session renewal failed, keeping previous cookie: %s", err)

    def get_thermostat(self, device_id: str) -> Any:
        """Return a thermostat from the latest snapshot, or None."""
        if self.data is None:
            return None
        return self.data.thermostats.get(device_id)
