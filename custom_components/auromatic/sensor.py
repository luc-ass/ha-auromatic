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
from .ebusd import sum_fields
from .entity import AuromaticEntity, CircuitMixin

# Icons stehen in icons.json und nur dort, wo keine device_class ein Symbol
# liefert. Wo es eine gibt, wählt Home Assistant zustandsabhängig aus -- das
# ist ausdrücklich der bevorzugte Weg, ein eigenes Icon wäre ein Rückschritt.

# Kürzel für die immer gleichen Temperatur-Argumente.
_TEMP = {
    "device_class": SensorDeviceClass.TEMPERATURE,
    "native_unit_of_measurement": UnitOfTemperature.CELSIUS,
    "state_class": SensorStateClass.MEASUREMENT,
}


# Nur lesend -- alle Werte stammen aus einem Abruf des Koordinators.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class AuromaticSensorDescription(SensorEntityDescription, CircuitMixin):
    """Sensorbeschreibung mit optionaler Sonderauswertung."""

    # Bekommt die unzerlegte Nachricht, nicht das einzelne Feld: gemeint sind
    # Auswertungen über alle Felder, etwa die Jahressumme der Monatserträge.
    value_fn: Callable[[str], StateType] | None = None
    # Bei mehrfeldrigen Nachrichten die einzelnen Felder als Attribute zeigen.
    expose_raw: bool = False


SENSORS: tuple[AuromaticSensorDescription, ...] = (
    # --- Heizkreis (0x26) ---------------------------------------------------
    AuromaticSensorDescription(
        key="outside_temp", circuit="hc", message="OutsideTemp",
        status_field=1, suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        key="sum_flow", circuit="hc", message="SumFlowSensor",
        status_field=1, suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        key="flow_desired", circuit="hc", message="FlowTempDesired",
        suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        key="flow_max", circuit="hc", message="FlowTempMax",
        entity_category=EntityCategory.DIAGNOSTIC, **_TEMP,
    ),
    AuromaticSensorDescription(
        # Der Regler kennt mit "disabled" einen Zustand, den das Bedienelement
        # nicht anbieten darf -- deshalb als Diagnose, nicht als zweiter Hebel.
        key="mode_state", circuit="hc", message="OperatingMode",
        device_class=SensorDeviceClass.ENUM,
        options=[*MODE_OPTIONS, "disabled"],
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # --- Fußbodenheizung / Mischerkreis (0x50) ------------------------------
    AuromaticSensorDescription(
        key="flow_temp", circuit="mc", message="FlowTemp",
        status_field=1, suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        key="flow_desired", circuit="mc", message="FlowTempDesired",
        suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        key="flow_max", circuit="mc", message="FlowTempMax",
        entity_category=EntityCategory.DIAGNOSTIC, **_TEMP,
    ),
    AuromaticSensorDescription(
        key="mode_state", circuit="mc", message="OperatingMode",
        device_class=SensorDeviceClass.ENUM,
        options=[*MODE_OPTIONS, "disabled"],
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    AuromaticSensorDescription(
        key="room_offset", circuit="mc", message="RoomTempOffset",
        native_unit_of_measurement=UnitOfTemperature.KELVIN,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # --- Warmwasser (0x25) --------------------------------------------------
    AuromaticSensorDescription(
        key="storage_temp", circuit="hwc", message="Storage1Sensor2",
        status_field=1, suggested_display_precision=1, **_TEMP,
    ),
    # --- Solar (0xec) -------------------------------------------------------
    AuromaticSensorDescription(
        key="collector_1", circuit="sc", message="Coll1Sensor",
        status_field=1, suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        key="collector_2", circuit="sc", message="Coll2Sensor",
        status_field=1, suggested_display_precision=1, **_TEMP,
    ),
    # Die vier Storage-Register sind nicht vier Speicherhöhen: der Regler
    # zeigt in Menü 6 „Speicherfühler 1..3" und danach „Fühler TD1/TD2".
    # Storage1..3 sind die Speicherfühler (1 oben, 2 unten, 3 hier nicht
    # angeschlossen), Storage4 ist TD1 aus der Differenztemperaturregelung --
    # kein Speicherfühler, deshalb auch keine monotone Solarladekurve.
    AuromaticSensorDescription(
        # Speicherfühler 1 (oben) und die Warmwasser-Speichertemperatur sind
        # derselbe Fühler: am 2026-09-02 über 140 zeitgleiche Messungen
        # verglichen, höchstens 0,24 K auseinander und zu 90 % unter 0,1 K.
        # Gelesen wird deshalb `hwc Storage1Sensor2`, das der Warmwasserkreis
        # ohnehin pollt; `sc Storage1Sensor3` ist aus dem Poll-Satz heraus und
        # gibt seinen Platz in der Warteschlange an die übrigen Messwerte ab.
        # Die Entität bleibt, wo sie hingehört: am Solarkreis, mit ihrem
        # Namen und ihrer Historie.
        key="storage_1", circuit="sc", source_circuit="hwc",
        message="Storage1Sensor2",
        status_field=1, suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        key="storage_2", circuit="sc", message="Storage2Sensor3",
        status_field=1, suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        key="storage_3", circuit="sc", message="Storage3Sensor3",
        status_field=1, suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        key="storage_4", circuit="sc", message="Storage4Sensor3",
        status_field=1, suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        # Trotz des Circuits `sc` kein Solarfühler: der Sammelrücklauf der
        # Heizung, Gegenstück zu `hc SumFlowSensor` (Sammelvorlauf). Er stand
        # am 2026-09-01 über 14 Stunden bei 26,1–26,5 °C, quer durch sechs
        # Pumpenzyklen bei bis zu 77 °C Kollektor -- Kellerniveau, weil der
        # Brenner abgeschaltet ist. Angeschlossen ist er (Status `ok`).
        key="backflow", circuit="sc", message="SumBackflowSensor",
        status_field=1, suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        # Der Ertragsfühler sitzt im Solarrücklauf und heißt deshalb so in der
        # Oberfläche: er ist die kalte Seite der Ertragsrechnung (heiße Seite
        # ist der Kollektorfühler, Durchsatz `SolFlowRate` = 3,50 l/min). Über
        # den Pumpenbetrieb folgt er dem Speicher unten (+1,3 K ± 1,9 K), nicht
        # dem Kollektor (−10,6 K ± 4,6 K), und fällt zweimal sogar darunter.
        # Einen Vorlauffühler hat der Solarkreis nicht.
        key="yield_sensor", circuit="sc", message="YieldSensor",
        status_field=1, suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        key="pump_hours", circuit="sc", message="CollPumpHRuntime1",
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # --- Bedienteil / Systemebene (0x15) ------------------------------------
    # `ui FlowTemp` hatte hier einen eigenen Sensor ("Systemvorlauf"). Er ist
    # entfallen: der Wert ist derselbe wie `hc SumFlowSensor` (Sammelvorlauf) --
    # identische Wertfolge, auseinander nur um den Zeitversatz der beiden
    # Abrufe --, und den hält das Bedienteil ohne unser Zutun frisch. Zwei
    # Entitäten für eine Temperatur sind eine zu viel, und zwei Register dafür
    # zwei zu viel.
    AuromaticSensorDescription(
        key="system_mode", circuit="ui", message="SystemModeStream1",
        device_class=SensorDeviceClass.ENUM,
        options=["heat", "off", "water", "cool"],
    ),
    AuromaticSensorDescription(
        # Der Fühler sitzt im Heizungsraum -- als Führungsgröße für das Haus
        # ist er unbrauchbar, deshalb ist der Name bewusst eindeutig.
        key="controller_room_temp", circuit="ui", message="RoomTemp",
        status_field=1, entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        key="boiler_hours", circuit="ui", message="BoilerHoursB1",
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    AuromaticSensorDescription(
        key="yield_year", circuit="ui", message="YieldThisYear",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        # Der Zähler fällt im Januar auf null zurück. TOTAL_INCREASING erkennt
        # genau das; TOTAL würde ohne last_reset falsch aufsummieren.
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=sum_fields, expose_raw=True,
    ),
    AuromaticSensorDescription(
        key="yield_last_year", circuit="ui", message="YieldLastYear",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        # Kein Zähler, sondern ein feststehender Jahreswert: ohne state_class,
        # damit er nicht als Verbrauch in die Langzeitstatistik einfließt.
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=sum_fields, expose_raw=True,
    ),
)

_MONTHS = (
    "januar", "februar", "märz", "april", "mai", "juni",
    "juli", "august", "september", "oktober", "november", "dezember",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AuromaticConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Sensoren anlegen -- nur für Register, die auch wirklich antworten."""
    coordinator = entry.runtime_data
    async_add_entities(
        AuromaticSensor(coordinator, description, entry.entry_id)
        for description in SENSORS
        # Nicht verbaute Fühler melden "cutoff" und werden hier aussortiert,
        # statt später dauerhaft als "unavailable" herumzustehen.
        if coordinator.value(description.source, description.message,
                             description.field, description.status_field) is not None
    )


class AuromaticSensor(AuromaticEntity, SensorEntity):
    """Ein einzelner Messwert."""

    entity_description: AuromaticSensorDescription

    @property
    def native_value(self) -> StateType:
        if self.entity_description.value_fn is not None:
            # Die ganze Nachricht, nicht raw_value: das wäre bei der
            # Ertragsstatistik nur das erste Feld und damit der Januar.
            raw = self.raw_message
            return self.entity_description.value_fn(raw) if raw is not None else None
        raw = self.raw_value
        if raw is None:
            return None
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
        raw = self.raw_message
        if raw is None:
            return None
        return dict(zip(_MONTHS, raw.split(";"), strict=False))
