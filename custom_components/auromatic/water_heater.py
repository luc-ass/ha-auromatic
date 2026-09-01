"""Warmwasserspeicher als eigene Entitaet statt als lose Sensoren."""

from __future__ import annotations

from typing import Any

from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import MODE_OPTIONS
from .coordinator import AuromaticConfigEntry
from .ebusd import EbusdError
from .entity import AuromaticEntity, CircuitDescription

DESCRIPTION = CircuitDescription(
    key="hot_water", circuit="hwc", message="Storage1Sensor2",
    status_field=1, name=None,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AuromaticConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    if coordinator.value("hwc", "Storage1Sensor2", status_index=1) is None:
        return
    async_add_entities([AuromaticWaterHeater(coordinator, DESCRIPTION, entry.entry_id)])


class AuromaticWaterHeater(AuromaticEntity, WaterHeaterEntity):
    """Speichertemperatur, Sollwert und Betriebsart in einer Karte."""

    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_operation_list = MODE_OPTIONS
    _attr_min_temp = 35.0
    _attr_max_temp = 70.0
    _attr_supported_features = (
        WaterHeaterEntityFeature.TARGET_TEMPERATURE
        | WaterHeaterEntityFeature.OPERATION_MODE
    )

    @property
    def current_temperature(self) -> float | None:
        raw = self.raw_value
        return float(raw) if raw is not None else None

    @property
    def target_temperature(self) -> float | None:
        raw = self.coordinator.value("hwc", "TempDesired2")
        return float(raw) if raw is not None else None

    @property
    def current_operation(self) -> str | None:
        raw = self.coordinator.value("hwc", "OperatingMode2")
        return raw if raw in MODE_OPTIONS else None

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        await self._write("SetTempDesired", f"{temperature:.1f}")

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        await self._write("SetMode", operation_mode)

    async def _write(self, message: str, value: str) -> None:
        try:
            await self.coordinator.client.write("hwc", message, value)
        except EbusdError as err:
            raise HomeAssistantError(f"Warmwasser konnte nicht gesetzt werden: {err}") from err
        await self.coordinator.async_request_refresh()
