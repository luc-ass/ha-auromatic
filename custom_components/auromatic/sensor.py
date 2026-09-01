"""Messwerte des Reglers als Sensoren."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfEnergy,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import MODE_OPTIONS
from .coordinator import AuromaticConfigEntry
from .entity import AuromaticEntity, CircuitMixin

# Kuerzel fuer die immer gleichen Temperatur-Argumente.
_TEMP = {
    "device_class": SensorDeviceClass.TEMPERATURE,
    "native_unit_of_measurement": UnitOfTemperature.CELSIUS,
    "state_class": SensorStateClass.MEASUREMENT,
}


def _sum_months(raw: str) -> StateType:
    """Zwoelf Monatswerte der Ertragsstatistik zur Jahressumme addieren."""
    try:
        return sum(int(part) for part in raw.split(";"))
    except ValueError:
        return None


@dataclass(frozen=True, kw_only=True)
class AuromaticSensorDescription(SensorEntityDescription, CircuitMixin):
    """Sensorbeschreibung mit optionaler Sonderauswertung."""

    value_fn: Callable[[str], StateType] | None = None
    # Bei mehrfeldrigen Nachrichten die restlichen Felder als Attribute zeigen.
    expose_raw: bool = False


SENSORS: tuple[AuromaticSensorDescription, ...] = (
    # --- Heizkreis (0x26) ---------------------------------------------------
    AuromaticSensorDescription(
        key="outside_temp", circuit="hc", message="OutsideTemp",
        status_field=1, name="Aussentemperatur",
        suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        key="sum_flow", circuit="hc", message="SumFlowSensor",
        status_field=1, name="Sammelvorlauf",
        suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        key="flow_desired", circuit="hc", message="FlowTempDesired",
        name="Vorlauf Soll", suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        key="flow_max", circuit="hc", message="FlowTempMax",
        name="Vorlauf Maximum", entity_category=EntityCategory.DIAGNOSTIC, **_TEMP,
    ),
    AuromaticSensorDescription(
        key="mode_state", circuit="hc", message="OperatingMode",
        name="Betriebsart", device_class=SensorDeviceClass.ENUM,
        options=[*MODE_OPTIONS, "disabled"],
    ),
    # --- Fussbodenheizung / Mischerkreis (0x50) -----------------------------
    AuromaticSensorDescription(
        key="flow_temp", circuit="mc", message="FlowTemp",
        status_field=1, name="Vorlauf",
        suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        key="flow_desired", circuit="mc", message="FlowTempDesired",
        name="Vorlauf Soll", suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        key="flow_max", circuit="mc", message="FlowTempMax",
        name="Vorlauf Maximum", entity_category=EntityCategory.DIAGNOSTIC, **_TEMP,
    ),
    AuromaticSensorDescription(
        key="mode_state", circuit="mc", message="OperatingMode",
        name="Betriebsart", device_class=SensorDeviceClass.ENUM,
        options=[*MODE_OPTIONS, "disabled"],
    ),
    AuromaticSensorDescription(
        key="room_offset", circuit="mc", message="RoomTempOffset",
        name="Raumtemperatur-Korrektur", native_unit_of_measurement=UnitOfTemperature.KELVIN,
        state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # --- Warmwasser (0x25) --------------------------------------------------
    AuromaticSensorDescription(
        key="storage_temp", circuit="hwc", message="Storage1Sensor2",
        status_field=1, name="Speichertemperatur",
        suggested_display_precision=1, **_TEMP,
    ),
    # --- Solar (0xec) -------------------------------------------------------
    AuromaticSensorDescription(
        key="collector_1", circuit="sc", message="Coll1Sensor",
        status_field=1, name="Kollektor", suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        key="collector_2", circuit="sc", message="Coll2Sensor",
        status_field=1, name="Kollektor 2", suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        key="storage_1", circuit="sc", message="Storage1Sensor3",
        status_field=1, name="Speicher oben", suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        key="storage_2", circuit="sc", message="Storage2Sensor3",
        status_field=1, name="Speicher Mitte", suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        key="storage_3", circuit="sc", message="Storage3Sensor3",
        status_field=1, name="Speicher 3", suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        key="storage_4", circuit="sc", message="Storage4Sensor3",
        status_field=1, name="Speicher unten", suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        key="backflow", circuit="sc", message="SumBackflowSensor",
        status_field=1, name="Solarruecklauf", suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        key="yield_sensor", circuit="sc", message="YieldSensor",
        status_field=1, name="Ertragsfuehler", suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        key="pump_hours", circuit="sc", message="CollPumpHRuntime1",
        name="Betriebsstunden Kollektorpumpe",
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    AuromaticSensorDescription(
        key="pump_duty", circuit="sc", message="SolCollPumpED1",
        name="Einschaltdauer Kollektorpumpe",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    # --- Bedienteil / Systemebene (0x15) ------------------------------------
    AuromaticSensorDescription(
        key="system_flow", circuit="ui", message="FlowTemp",
        status_field=1, name="Systemvorlauf", suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        key="system_mode", circuit="ui", message="SystemModeStream1",
        name="Systemzustand", device_class=SensorDeviceClass.ENUM,
        options=["heat", "off", "water", "cool"],
    ),
    AuromaticSensorDescription(
        # Der Fuehler sitzt im Heizungsraum -- als Fuehrungsgroesse fuer das
        # Haus ist er unbrauchbar, deshalb ist der Name bewusst eindeutig.
        key="controller_room_temp", circuit="ui", message="RoomTemp",
        status_field=1, name="Raumfuehler Heizungsraum",
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        key="boiler_hours", circuit="ui", message="BoilerHoursB1",
        name="Betriebsstunden Kessel",
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    AuromaticSensorDescription(
        key="yield_year", circuit="ui", message="YieldThisYear",
        name="Solarertrag laufendes Jahr",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        value_fn=_sum_months, expose_raw=True,
    ),
    AuromaticSensorDescription(
        key="yield_last_year", circuit="ui", message="YieldLastYear",
        name="Solarertrag Vorjahr",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        value_fn=_sum_months, expose_raw=True,
    ),
)

_MONTHS = (
    "januar", "februar", "maerz", "april", "mai", "juni",
    "juli", "august", "september", "oktober", "november", "dezember",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AuromaticConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Sensoren anlegen -- nur fuer Register, die auch wirklich antworten."""
    coordinator = entry.runtime_data
    async_add_entities(
        AuromaticSensor(coordinator, description, entry.entry_id)
        for description in SENSORS
        # Nicht verbaute Fuehler melden "cutoff" und werden hier aussortiert,
        # statt spaeter dauerhaft als "unavailable" herumzustehen.
        if coordinator.value(description.circuit, description.message,
                             description.field, description.status_field) is not None
    )


class AuromaticSensor(AuromaticEntity, SensorEntity):
    """Ein einzelner Messwert."""

    entity_description: AuromaticSensorDescription

    @property
    def native_value(self) -> StateType:
        raw = self.raw_value
        if raw is None:
            return None
        if self.entity_description.value_fn is not None:
            return self.entity_description.value_fn(raw)
        if self.entity_description.device_class is SensorDeviceClass.ENUM:
            return raw
        try:
            return float(raw)
        except ValueError:
            return raw

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        if not self.entity_description.expose_raw:
            return None
        raw = self.raw_value
        if raw is None:
            return None
        return dict(zip(_MONTHS, raw.split(";"), strict=False))
