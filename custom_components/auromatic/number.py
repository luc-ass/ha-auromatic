"""Sollwerte, die sich gefahrlos verstellen lassen."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import AuromaticConfigEntry
from .ebusd import EbusdError
from .entity import AuromaticEntity, CircuitMixin


@dataclass(frozen=True, kw_only=True)
class AuromaticNumberDescription(NumberEntityDescription, CircuitMixin):
    """Ein Sollwert mit eigener Schreibnachricht und fester Nachkommastelle."""

    write_message: str
    # Wie viele Nachkommastellen der Regler fuer dieses Register erwartet.
    decimals: int = 1


_ROOM = {
    "native_min_value": 5.0,
    "native_max_value": 30.0,
    "native_step": 0.5,
    "native_unit_of_measurement": UnitOfTemperature.CELSIUS,
    "device_class": NumberDeviceClass.TEMPERATURE,
}

NUMBERS: tuple[AuromaticNumberDescription, ...] = (
    AuromaticNumberDescription(
        key="temp_desired", circuit="hc", message="TempDesired",
        write_message="SetTempDesired", name="Raumsoll Tag", **_ROOM,
    ),
    AuromaticNumberDescription(
        key="temp_desired_low", circuit="hc", message="TempDesiredLow",
        write_message="SetTempDesiredLow", name="Raumsoll Absenkung", **_ROOM,
    ),
    AuromaticNumberDescription(
        key="temp_desired", circuit="mc", message="TempDesired",
        write_message="SetTempDesired", name="Raumsoll Tag", **_ROOM,
    ),
    AuromaticNumberDescription(
        key="temp_desired_low", circuit="mc", message="TempDesiredLow",
        write_message="SetTempDesiredLow", name="Raumsoll Absenkung", **_ROOM,
    ),
    AuromaticNumberDescription(
        key="heating_curve", circuit="hc", message="HeatingCurve",
        write_message="SetHeatingCurve", name="Heizkurve",
        native_min_value=0.0, native_max_value=4.0, native_step=0.05, decimals=2,
    ),
    AuromaticNumberDescription(
        key="heating_curve", circuit="mc", message="HeatingCurve",
        write_message="SetHeatingCurve", name="Heizkurve",
        native_min_value=0.0, native_max_value=1.5, native_step=0.05, decimals=2,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AuromaticConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        AuromaticNumber(coordinator, description, entry.entry_id)
        for description in NUMBERS
        if coordinator.value(description.circuit, description.message) is not None
    )


class AuromaticNumber(AuromaticEntity, NumberEntity):
    """Ein schreibbarer Sollwert."""

    entity_description: AuromaticNumberDescription
    _attr_mode = NumberMode.BOX

    @property
    def native_value(self) -> float | None:
        raw = self.raw_value
        if raw is None:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    async def async_set_native_value(self, value: float) -> None:
        description = self.entity_description
        try:
            await self.coordinator.client.write(
                description.circuit, description.write_message, f"{value:.{description.decimals}f}"
            )
        except EbusdError as err:
            raise HomeAssistantError(f"Wert konnte nicht geschrieben werden: {err}") from err
        await self.coordinator.async_request_refresh()
