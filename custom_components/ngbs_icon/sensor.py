"""Sensor platform for NGBS iCON controller and thermostat extras."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client import HomeState, Thermostat
from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import NgbsCoordinator


@dataclass(frozen=True, kw_only=True)
class NgbsHomeSensorDescription(SensorEntityDescription):
    """Description of a controller-level sensor."""

    value_fn: Callable[[HomeState], StateType]


@dataclass(frozen=True, kw_only=True)
class NgbsThermostatSensorDescription(SensorEntityDescription):
    """Description of a per-thermostat sensor."""

    value_fn: Callable[[Thermostat], StateType]


HOME_SENSORS: tuple[NgbsHomeSensorDescription, ...] = (
    NgbsHomeSensorDescription(
        key="water_temperature",
        translation_key="water_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda home: home.water_temp,
    ),
    NgbsHomeSensorDescription(
        key="external_temperature",
        translation_key="external_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda home: home.external_temp,
    ),
    NgbsHomeSensorDescription(
        key="battery",
        translation_key="battery",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda home: home.battery,
    ),
)

THERMOSTAT_SENSORS: tuple[NgbsThermostatSensorDescription, ...] = (
    NgbsThermostatSensorDescription(
        key="humidity",
        translation_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda t: t.rh,
    ),
    NgbsThermostatSensorDescription(
        key="dew_point",
        translation_key="dew_point",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda t: t.dew,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors from a config entry."""
    coordinator: NgbsCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [
        NgbsHomeSensor(coordinator, description) for description in HOME_SENSORS
    ]
    for device_id in coordinator.data.thermostats:
        entities.extend(
            NgbsThermostatSensor(coordinator, device_id, description)
            for description in THERMOSTAT_SENSORS
        )
    async_add_entities(entities)


class NgbsHomeSensor(CoordinatorEntity[NgbsCoordinator], SensorEntity):
    """Controller-level diagnostic / environment sensor."""

    _attr_has_entity_name = True
    entity_description: NgbsHomeSensorDescription

    def __init__(
        self,
        coordinator: NgbsCoordinator,
        description: NgbsHomeSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        icon_id = coordinator.client.icon_id
        self._attr_unique_id = f"{icon_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, icon_id)},
            "manufacturer": MANUFACTURER,
            "model": MODEL,
            "name": coordinator.data.name or f"NGBS iCON {icon_id}",
        }

    @property
    def native_value(self) -> StateType:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        """Unavailable when the value is missing."""
        return (
            super().available
            and self.entity_description.value_fn(self.coordinator.data) is not None
        )


class NgbsThermostatSensor(CoordinatorEntity[NgbsCoordinator], SensorEntity):
    """Per-room sensor attached to a thermostat device."""

    _attr_has_entity_name = True
    entity_description: NgbsThermostatSensorDescription

    def __init__(
        self,
        coordinator: NgbsCoordinator,
        device_id: str,
        description: NgbsThermostatSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._device_id = device_id
        icon_id = coordinator.client.icon_id
        thermostat = coordinator.data.thermostats[device_id]
        self._attr_unique_id = f"{icon_id}_{device_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{icon_id}_{device_id}")},
            "name": thermostat.title,
            "manufacturer": MANUFACTURER,
            "model": MODEL,
            "serial_number": device_id,
            "via_device": (DOMAIN, icon_id),
        }

    @property
    def native_value(self) -> StateType:
        """Return the sensor value."""
        thermostat = self.coordinator.get_thermostat(self._device_id)
        if thermostat is None:
            return None
        return self.entity_description.value_fn(thermostat)

    @property
    def available(self) -> bool:
        """Unavailable when the thermostat or value is missing."""
        thermostat = self.coordinator.get_thermostat(self._device_id)
        return (
            super().available
            and thermostat is not None
            and self.entity_description.value_fn(thermostat) is not None
        )
