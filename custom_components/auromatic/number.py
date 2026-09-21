"""Sollwerte, die sich gefahrlos verstellen lassen."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import AuromaticConfigEntry
from .ebusd import EbusdError
from .entity import AuromaticEntity, CircuitMixin, async_add_available


# Schreibend: der eBUS ist langsam, Befehle laufen nacheinander.
PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class AuromaticNumberDescription(NumberEntityDescription, CircuitMixin):
    """Ein Sollwert mit eigener Schreibnachricht und fester Nachkommastelle."""

    # Fast immer der Nachrichtenname selbst: die Register sind in der
    # ebusd-Konfiguration als "r;w" deklariert, Lese- und Schreibvariante
    # heißen gleich. Bleibt als eigenes Feld stehen, damit der Schreibweg an
    # der Beschreibung ablesbar ist und nicht implizit mitgeraten wird.
    write_message: str
    # Wie viele Nachkommastellen der Regler für dieses Register erwartet.
    decimals: int = 1


_ROOM = {
    "native_min_value": 5.0,
    "native_max_value": 30.0,
    "native_step": 0.5,
    "native_unit_of_measurement": UnitOfTemperature.CELSIUS,
    "device_class": NumberDeviceClass.TEMPERATURE,
}

# Temperaturdifferenzen, keine Temperaturen: Kelvin ohne device_class, sonst
# rechnet Home Assistant sie in Fahrenheit um. Der Regler nimmt hier nur ganze
# Kelvin an (Datentyp "temp0").
#
# Die Kategorie stand bis zum 2026-09-03 mit in diesem Bündel und war damit an
# der Beschreibung nicht ablesbar -- weder für einen Leser noch für
# tests/test_translations.py, das die Beschreibungen per `ast` ausliest. Sie
# steht jetzt bei jeder Entität einzeln.
_DIFF = {
    "native_unit_of_measurement": UnitOfTemperature.KELVIN,
    "native_step": 1.0,
}

NUMBERS: tuple[AuromaticNumberDescription, ...] = (
    AuromaticNumberDescription(
        key="temp_desired", circuit="hc", message="TempDesired",
        write_message="TempDesired", **_ROOM,
    ),
    AuromaticNumberDescription(
        key="temp_desired_low", circuit="hc", message="TempDesiredLow",
        write_message="TempDesiredLow", **_ROOM,
    ),
    AuromaticNumberDescription(
        key="temp_desired", circuit="mc", message="TempDesired",
        write_message="TempDesired", **_ROOM,
    ),
    AuromaticNumberDescription(
        key="temp_desired_low", circuit="mc", message="TempDesiredLow",
        write_message="TempDesiredLow", **_ROOM,
    ),
    # Die Heizkurve ist kein Wert des Alltags: sie legt die Auslegung des
    # Kreises fest und wird einmal eingestellt, nicht nach Bedarf gedreht.
    # Deshalb Konfiguration -- neben ihr stehen im Alltag nur die beiden
    # Raumsollwerte und die Betriebsart.
    AuromaticNumberDescription(
        key="heating_curve", circuit="hc", message="HeatingCurve",
        write_message="HeatingCurve", entity_category=EntityCategory.CONFIG,
        native_min_value=0.0, native_max_value=4.0, native_step=0.05, decimals=2,
    ),
    AuromaticNumberDescription(
        key="heating_curve", circuit="mc", message="HeatingCurve",
        write_message="HeatingCurve", entity_category=EntityCategory.CONFIG,
        native_min_value=0.0, native_max_value=1.5, native_step=0.05, decimals=2,
    ),
    # --- Solarkreis (0xec) --------------------------------------------------
    # Die Kollektorpumpe schaltet nach der Differenz zwischen Kollektor 1 und
    # Speicher unten: ein ab SolEnableDiffTemp1, aus unter SolDisableDiffTemp1.
    # Die Einschaltschwelle muss über der Ausschaltschwelle bleiben, sonst
    # taktet die Pumpe. Die Grenzen hier lassen die beiden Bereiche bewusst
    # überlappen -- eine kreuzweise Prüfung gehört an den Regler, nicht in zwei
    # unabhängige Bedienelemente.
    AuromaticNumberDescription(
        key="sol_enable_diff", circuit="sc", message="SolEnableDiffTemp1",
        write_message="SolEnableDiffTemp1", decimals=0,
        entity_category=EntityCategory.CONFIG,
        native_min_value=5.0, native_max_value=25.0, **_DIFF,
    ),
    AuromaticNumberDescription(
        key="sol_disable_diff", circuit="sc", message="SolDisableDiffTemp1",
        write_message="SolDisableDiffTemp1", decimals=0,
        entity_category=EntityCategory.CONFIG,
        native_min_value=2.0, native_max_value=15.0, **_DIFF,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AuromaticConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_available(
        entry, async_add_entities, NUMBERS,
        lambda description: AuromaticNumber(coordinator, description, entry.entry_id),
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
            await self.coordinator.async_write(
                description.circuit,
                description.write_message,
                f"{value:.{description.decimals}f}",
                description.message,
            )
        except EbusdError as err:
            raise HomeAssistantError(f"Wert konnte nicht geschrieben werden: {err}") from err
