"""Climate platform for NGBS iCON thermostats."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client import NgbsClientClosed, NgbsWriteSuperseded, Thermostat
from .const import ATTR_CE, ATTR_HC, ATTR_REQ, DOMAIN, MANUFACTURER, MODEL, SWITCHOVER_POLLS
from .coordinator import NgbsCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up climate entities from a config entry."""
    coordinator: NgbsCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        NgbsClimateEntity(coordinator, device_id)
        for device_id in coordinator.data.thermostats
    ]
    async_add_entities(entities)


class NgbsClimateEntity(CoordinatorEntity[NgbsCoordinator], ClimateEntity):
    """One NGBS iCON room thermostat as a climate entity."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.AUTO]

    def __init__(self, coordinator: NgbsCoordinator, device_id: str) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._pending_switchover: dict[str, Any] | None = None
        self._switchover_rejected = False
        self._remembered_mode: HVACMode | None = None

        thermostat = coordinator.data.thermostats[device_id]
        self._attr_unique_id = f"{coordinator.client.icon_id}_{device_id}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{coordinator.client.icon_id}_{device_id}")},
            "name": thermostat.title,
            "manufacturer": MANUFACTURER,
            "model": MODEL,
            "serial_number": device_id,
            "via_device": (DOMAIN, coordinator.client.icon_id),
        }
        self._sync_from_thermostat(thermostat)

    @property
    def thermostat(self) -> Thermostat | None:
        """Latest thermostat snapshot."""
        return self.coordinator.get_thermostat(self._device_id)

    @callback
    def _handle_coordinator_update(self) -> None:
        thermostat = self.thermostat
        if thermostat is None:
            super()._handle_coordinator_update()
            return
        self._check_switchover(thermostat)
        self._sync_from_thermostat(thermostat)
        super()._handle_coordinator_update()

    def _sync_from_thermostat(self, thermostat: Thermostat) -> None:
        cooling = self.coordinator.data.is_cooling(thermostat)
        self._attr_current_temperature = thermostat.temp
        self._attr_target_temperature = thermostat.req
        self._attr_current_humidity = thermostat.rh
        self._attr_min_temp = thermostat.tmin if thermostat.tmin is not None else 5
        self._attr_max_temp = thermostat.tmax if thermostat.tmax is not None else 35

        if thermostat.out == 1:
            self._attr_hvac_action = (
                HVACAction.COOLING if cooling else HVACAction.HEATING
            )
        elif thermostat.ce == 1:
            self._attr_hvac_action = HVACAction.OFF
        else:
            self._attr_hvac_action = HVACAction.IDLE

        if thermostat.ce == 1:
            self._attr_hvac_mode = HVACMode.OFF
        else:
            self._attr_hvac_mode = self._on_mode(cooling)

        self._attr_extra_state_attributes = {
            "thermostat_id": thermostat.id,
            "eco_mode": thermostat.ce == 1,
            "valve_open": thermostat.out == 1,
            "season": "cooling" if cooling else "heating",
            "dew_point": thermostat.dew,
            "switchover_mode": self.coordinator.data.switchover_mode,
            "switchover_master": self.coordinator.data.switchover_master,
        }

    def _on_mode(self, cooling: bool) -> HVACMode:
        """Mode shown while the thermostat is on (not Eco)."""
        if (
            self._remembered_mode is not None
            and self._switchover_allowed()
            and self._remembered_mode in (HVACMode.HEAT, HVACMode.COOL)
        ):
            return self._remembered_mode
        return HVACMode.COOL if cooling else HVACMode.HEAT

    def _switchover_allowed(self) -> bool:
        return self.coordinator.manual_hc_switch and not self._switchover_rejected

    def _check_switchover(self, thermostat: Thermostat) -> None:
        if self._pending_switchover is None:
            return
        cooling = self.coordinator.data.is_cooling(thermostat)
        if cooling == self._pending_switchover["cooling"]:
            self._pending_switchover = None
            return
        self._pending_switchover["polls"] += 1
        if self._pending_switchover["polls"] < SWITCHOVER_POLLS:
            return

        self._pending_switchover = None
        self._switchover_rejected = True
        self._remembered_mode = None
        hint_parts = []
        if self.coordinator.data.switchover_mode:
            hint_parts.append(f"mode: {self.coordinator.data.switchover_mode}")
        if self.coordinator.data.switchover_master:
            hint_parts.append(f"master: {self.coordinator.data.switchover_master}")
        hint = f" ({', '.join(hint_parts)})" if hint_parts else ""
        _LOGGER.warning(
            "%s cannot switch between heating and cooling on its own%s. "
            "Falling back to whatever season the system is running.",
            self.name or self._device_id,
            hint,
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature; leaving Eco if needed."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        rounded = round(float(temperature) / 0.5) * 0.5
        thermostat = self.thermostat
        client = self.coordinator.client

        requests = []
        if thermostat is None or thermostat.ce != 0:
            requests.append(client.set_attr(self._device_id, ATTR_CE, "0"))
        if thermostat is None or thermostat.req != rounded:
            requests.append(
                client.set_attr(self._device_id, ATTR_REQ, _format_temp(rounded))
            )

        if thermostat is not None:
            thermostat.apply({"CE": 0, "REQ": rounded})
            self._sync_from_thermostat(thermostat)
            self.async_write_ha_state()

        if requests:
            try:
                await _await_writes(requests)
            except NgbsClientClosed:
                return
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "Failed to set temperature of %s", self.name or self._device_id
                )
                await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode. Off maps to Eco; Heat/Cool/Auto mean on (+ optional HC)."""
        thermostat = self.thermostat
        client = self.coordinator.client
        eco = 1 if hvac_mode == HVACMode.OFF else 0
        previous_ce = None if thermostat is None else thermostat.ce
        previous_cooling = (
            self.coordinator.data.is_cooling(thermostat)
            if thermostat is not None and self.coordinator.data is not None
            else False
        )

        switching = (
            hvac_mode in (HVACMode.HEAT, HVACMode.COOL) and self._switchover_allowed()
        )
        if hvac_mode != HVACMode.OFF:
            self._remembered_mode = hvac_mode if switching else None

        want_cooling = hvac_mode == HVACMode.COOL
        changes: dict[str, Any] = {"CE": eco}
        writes: list[Any] = []

        if previous_ce != eco:
            writes.append(client.set_attr(self._device_id, ATTR_CE, str(eco)))

        if switching and previous_cooling != want_cooling:
            changes["HC"] = 1 if want_cooling else 0
            self._pending_switchover = {"cooling": want_cooling, "polls": 0}
            writes.append(
                client.set_attr(
                    self._device_id, ATTR_HC, "1" if want_cooling else "0"
                )
            )

        # Optimistic update before the cloud round-trip so a superseded write
        # cannot apply stale state after a newer command has already run.
        if thermostat is not None:
            thermostat.apply(changes)
            self._sync_from_thermostat(thermostat)
            self.async_write_ha_state()

        if not writes:
            return

        try:
            await _await_writes(writes)
        except NgbsClientClosed:
            return
        except Exception:  # noqa: BLE001
            _LOGGER.exception(
                "Failed to set mode of %s", self.name or self._device_id
            )
            await self.coordinator.async_request_refresh()

    async def async_turn_on(self) -> None:
        """Leave Eco mode."""
        mode = self._remembered_mode or (
            HVACMode.COOL
            if self.coordinator.data
            and self.thermostat
            and self.coordinator.data.is_cooling(self.thermostat)
            else HVACMode.HEAT
        )
        await self.async_set_hvac_mode(mode)

    async def async_turn_off(self) -> None:
        """Enter Eco mode (stands in for Off)."""
        await self.async_set_hvac_mode(HVACMode.OFF)


def _format_temp(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return str(value)


async def _await_writes(writes: list[Any]) -> None:
    """Await queued writes; ignore values superseded by a newer command."""
    if not writes:
        return
    results = await asyncio.gather(*writes, return_exceptions=True)
    errors = [
        result
        for result in results
        if isinstance(result, Exception)
        and not isinstance(result, NgbsWriteSuperseded)
    ]
    if errors:
        raise errors[0]
