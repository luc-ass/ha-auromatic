"""Messwerte des Reglers als Sensoren."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfEnergy,
    UnitOfPressure,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import HWC_MODE_OPTIONS, MODE_OPTIONS, ROOT_DEVICE
from .coordinator import AuromaticConfigEntry
from .ebusd import parse_date, sum_fields
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
    value_fn: Callable[[str], StateType | date] | None = None
    # Bei mehrfeldrigen Nachrichten die einzelnen Felder als Attribute zeigen.
    expose_raw: bool = False
    # Ein zweites Register desselben Kreises, dessen Wert hinzuaddiert wird.
    #
    # Vaillant führt seine Schaltspielzähler zweigeteilt: `HcStarts` trägt in
    # der ebusd-Definition den Teiler -100, also den Faktor 100, und die
    # beiden fehlenden Stellen stehen in einem eigenen Register. Erst die
    # Summe ist der Stand, den das Gerät selbst führt. Gelesen wird aus
    # `source`, geschrieben wird darauf nie -- es ist ein reiner Zähler.
    plus_message: str | None = None


SENSORS: tuple[AuromaticSensorDescription, ...] = (
    # --- Wärmeerzeuger (0x08) -----------------------------------------------
    # Der Kessel ist kein Kreis des Reglers, sondern ein eigenes Gerät am Bus.
    # Er antwortet erst seit dem 2026-09-04 wieder; davor war er stromlos.
    #
    # Zwei seiner Nachrichten tragen mehrere Entitäten und kosten deshalb
    # weniger, als ihre Zahl vermuten lässt: `Status01` führt Vorlauf,
    # Rücklauf und Pumpenzustand, `SetMode` die Anforderung des Reglers. Die
    # Feldnummern stehen in `vaillant/hcmode.inc`.
    AuromaticSensorDescription(
        # Der Wert, wegen dem der Kessel überhaupt eingebunden ist: F.75 heißt
        # "kein Druckanstieg beim Anlaufen der Pumpe". Am 2026-09-04 stand der
        # Druck bei 1,461 bar -- die Ursache war die festsitzende Pumpe, nicht
        # Wassermangel. Wer den Verlauf sieht, kann beides unterscheiden.
        key="water_pressure", circuit="bai", message="WaterPressure",
        status_field=1,
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.BAR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    AuromaticSensorDescription(
        # `Status01` Feld 1. Der Datentyp ist `temp1` und löst deshalb nur
        # 0,5 K auf; das eigene Register `bai FlowTemp` (d.40) wäre feiner,
        # kostete aber einen weiteren Platz in der Warteschlange für dieselbe
        # Temperatur. 0,5 K sind hier reichlich genau.
        key="flow_temp", circuit="bai", message="Status01",
        suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        # `Status01` Feld 2 -- d.41 am Gerät.
        key="return_temp", circuit="bai", message="Status01", field=1,
        suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        # `SetMode` Feld 2: der Vorlaufsollwert, den der Regler an den Kessel
        # schickt. Am 2026-09-04 lief er über rund 70 Minuten von 25 auf
        # 39 °C -- das ist der Testlauf, in dem F.75 auftrat.
        key="flow_desired", circuit="bai", message="SetMode", field=1,
        suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        # Der Statuscode der Kesselanzeige (S.xx). 31 heißt "kein
        # Wärmebedarf". Kein ENUM: die Liste der Codes ist lang, steht in der
        # Installationsanleitung und nicht in der ebusd-Definition -- ein
        # unvollständiges Optionsfeld wäre schlechter als die nackte Zahl.
        key="state_number", circuit="bai", message="Statenumber",
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    AuromaticSensorDescription(
        key="boiler_hc_hours", circuit="bai", message="HcHours",
        suggested_display_precision=0,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    AuromaticSensorDescription(
        # d.82 am Gerät -- und ein Zähler in zwei Registern. `HcStarts` steht
        # in der ebusd-Definition mit dem Teiler -100, also dem Faktor 100 auf
        # einen Rohwert von 2647; die beiden fehlenden Stellen liefert
        # `HcUnderHundredStarts` ("Heat switch cycles under hundred").
        #
        # Ohne die Ergänzung steht der Zähler zwischen zwei Hundertern still:
        # am 2026-09-06 über einen nachgewiesenen Brennerzyklus hinweg, am
        # 2026-09-13 über eine ganze Woche samt Wartung. Das sah nach einem
        # defekten Zähler aus und war nur die Auflösung.
        #
        # Der Sprung 99 -> 0 im Restregister fällt mit dem Sprung um 100 im
        # Hauptregister zusammen, aber nicht notwendig im selben Abruf: beide
        # stehen getrennt in der Warteschlange. Für einen Zyklus kann die
        # Summe deshalb um bis zu 99 zurückfallen. TOTAL_INCREASING verträgt
        # das -- Home Assistant liest erst einen Rückgang unter 90 % des
        # letzten Wertes als Zählerneustart, und das sind hier 26 000.
        key="boiler_hc_starts", circuit="bai", message="HcStarts",
        plus_message="HcUnderHundredStarts",
        suggested_display_precision=0,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    AuromaticSensorDescription(
        # Die Pumpe, die am 2026-09-04 festsaß. Ihre beiden Zähler sind die
        # Vorgeschichte zu jeder künftigen Störung.
        key="boiler_pump_hours", circuit="bai", message="PumpHours",
        suggested_display_precision=0,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    AuromaticSensorDescription(
        key="boiler_pump_starts", circuit="bai", message="HcPumpStarts",
        suggested_display_precision=0,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    AuromaticSensorDescription(
        # d.84 am Gerät. Zählt herunter, ist also kein Zähler im Sinne der
        # Statistik -- TOTAL_INCREASING läse in jedem Wartungsintervall einen
        # Rücksprung als frischen Verbrauch.
        key="service_hours", circuit="bai", message="HoursTillService",
        suggested_display_precision=0,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    AuromaticSensorDescription(
        # d.61: Zündfehler über die Lebensdauer. Steht auf 1, bei 264 702
        # Schaltspielen -- der Brenner selbst ist in Ordnung.
        key="ignition_failures", circuit="bai", message="DeactivationsIFC",
        suggested_display_precision=0,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # --- Heizkreis (0x26) ---------------------------------------------------
    AuromaticSensorDescription(
        # Grunddaten der Anlage, nicht des Heizkreises: der Regler zeigt die
        # Außentemperatur im Kopf jeder Anzeige, und `ui OutsideTemp` liefert
        # zeitgleich denselben Wert.
        key="outside_temp", circuit="hc", device_circuit=ROOT_DEVICE,
        message="OutsideTemp",
        status_field=1, suggested_display_precision=1, **_TEMP,
    ),
    AuromaticSensorDescription(
        # Der Sammelvorlauf der Anlage, nicht der des Heizkreises -- Gegenstück
        # ist der Sammelrücklauf, der im Solarkreis steht. Beide am selben
        # Gerät, sonst sucht man das zweite beim falschen.
        key="sum_flow", circuit="hc", device_circuit=ROOT_DEVICE,
        message="SumFlowSensor",
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
    # --- Zirkulation (0x23) -------------------------------------------------
    AuromaticSensorDescription(
        # Dieselbe Aufgabe wie in den Heizkreisen, nur aus einem Feld der
        # Sammelnachricht: der Select kann "disabled" nicht anzeigen.
        key="mode_state", translation_key="circulation_mode_state",
        circuit="cc", message="Mode", field=1,
        device_class=SensorDeviceClass.ENUM,
        options=[*HWC_MODE_OPTIONS, "disabled"],
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
        # Liegt im Solarkreis, misst aber den Heizungsrücklauf (14-h-Messreihe,
        # kein Ausschlag bei sechs Pumpenzyklen). Gehört deshalb neben den
        # Sammelvorlauf ans Wurzelgerät; `circuit` bleibt `sc`, sonst ändert
        # sich die `unique_id` und die Historie ist weg.
        key="backflow", circuit="sc", device_circuit=ROOT_DEVICE,
        message="SumBackflowSensor",
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
    # Die Schutz- und Auslegungswerte des Solarkreises. Sie stehen bewusst hier
    # und nicht in number.py, obwohl alle fünf `r;w` sind und einzeln
    # geschrieben werden könnten: es sind die geräteseitigen Absicherungen des
    # Kollektorkreises, dieselbe Sorte Wert wie `FlowTempMax` am Mischerkreis,
    # den Invariante 3 aus demselben Grund schreibgeschützt lässt. Ein
    # Bedienelement lädt dazu ein, im Vorbeigehen an einer Übertemperaturgrenze
    # zu drehen; als Anzeige leisten sie, was gebraucht wird -- man sieht, wie
    # der Regler eingestellt ist, ohne ans Gerät zu laufen.
    #
    # Alle fünf stehen auf Stufe 9 im Poll-Satz -- die billigste Stelle, die
    # es gibt: die Warteschlange erzeugt keinen Mehrverkehr, sie verteilt ihn
    # nur um. Siehe poll.py.
    AuromaticSensorDescription(
        # Oberhalb dieser Kollektortemperatur schaltet der Regler die
        # Kollektorpumpe zum Schutz vor Überhitzung ab. Die Freigabe der
        # Funktion ist der Binärsensor `collector_protection`.
        key="sol_protection_start", circuit="sc", message="SolProtectionStartTemp",
        entity_category=EntityCategory.DIAGNOSTIC, **_TEMP,
    ),
    AuromaticSensorDescription(
        # Um so viel muss der Kollektor wieder abkühlen, bevor die
        # Schutzfunktion endet. Kelvin ohne device_class, sonst rechnet Home
        # Assistant die Differenz in Fahrenheit um -- wie bei `room_offset`.
        key="sol_protection_hysteresis", circuit="sc", message="ScProtectionHysteresis",
        native_unit_of_measurement=UnitOfTemperature.KELVIN,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    AuromaticSensorDescription(
        key="sol_max_load", circuit="sc", message="SolHwcMaxLoadTemp1",
        entity_category=EntityCategory.DIAGNOSTIC, **_TEMP,
    ),
    AuromaticSensorDescription(
        # Steht an dieser Anlage auf 0 und ist damit unwirksam.
        key="coll_temp_min", circuit="sc", message="KolTempMin1",
        entity_category=EntityCategory.DIAGNOSTIC, **_TEMP,
    ),
    AuromaticSensorDescription(
        # Kein Messwert, sondern die Auslegungsgröße, mit der der Regler selbst
        # rechnet: Durchsatz der Kollektorpumpe bei 100 % Leistung. Zusammen
        # mit der Spreizung Kollektor gegen Solarrücklauf ergibt sie die
        # Leistung, und sie geht in `YieldThisYear` ein.
        #
        # Zur Einheit: die aufgelöste CSV, die ebusd lädt, führt das Register
        # als `UIN` mit Teiler 60 in **l/min**; die neuere TypeSpec-Fassung
        # nennt einen Typ `flowrate` in l/h. Maßgeblich ist der Messwert --
        # 3,50 ist als l/min eine übliche Kollektorbestückung, als l/h wäre es
        # kein Solarkreis.
        key="sol_flow_rate", circuit="sc", message="SolFlowRate",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.LITERS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
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
        key="system_mode", circuit="ui", device_circuit=ROOT_DEVICE,
        message="SystemModeStream1",
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
        # "Ansteuerstunden Gerät 1" nennt es die Reglerdefinition -- gezählt
        # wird, wie lange der Regler Wärme angefordert hat, nicht wie lange ein
        # Kessel lief. An dieser Anlage ist der Brenner abgeschaltet und der
        # Zähler steht; "Betriebsstunden" wäre hier die falsche Auskunft.
        key="boiler_hours", circuit="ui", device_circuit=ROOT_DEVICE,
        message="BoilerHoursB1",
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    AuromaticSensorDescription(
        # Der Wartungstermin, den der Regler selbst führt -- nicht zu
        # verwechseln mit `service_hours` (d.84), dem Stundenzähler der
        # Therme. Beides ist dieselbe Frage aus zwei Richtungen: der Zähler
        # sagt, wie lange der Brenner noch darf, das Datum, wann jemand da
        # war.
        #
        # Am 2026-09-13 stand hier 10.09.2027, genau ein Jahr nach der
        # Wartung vom 10.09.2026 -- der Techniker hat ihn gesetzt. Damit ist
        # am Bus ablesbar, wann die Anlage zuletzt gewartet wurde, und das
        # war vorher nirgends sichtbar.
        #
        # `SensorDeviceClass.DATE` verlangt ein echtes Datum, deshalb
        # `parse_date`: ein nicht gesetzter Termin kommt als `-.-.-` und
        # ergibt dann keinen Wert statt einer Zeichenkette. Ein Icon steht
        # bewusst nicht in icons.json -- die device_class liefert eines.
        key="service_date", circuit="ui", device_circuit=ROOT_DEVICE,
        message="ServicePeriod",
        device_class=SensorDeviceClass.DATE,
        value_fn=parse_date,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    AuromaticSensorDescription(
        # Den Solarertrag führt das Bedienteil, gesucht wird er beim Solar.
        key="yield_year", circuit="ui", device_circuit="sc",
        message="YieldThisYear",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        # Der Zähler fällt im Januar auf null zurück. TOTAL_INCREASING erkennt
        # genau das; TOTAL würde ohne last_reset falsch aufsummieren.
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=sum_fields, expose_raw=True,
    ),
    AuromaticSensorDescription(
        key="yield_last_year", circuit="ui", device_circuit="sc",
        message="YieldLastYear",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        # Kein Zähler, sondern ein feststehender Jahreswert: ohne state_class,
        # damit er nicht als Verbrauch in die Langzeitstatistik einfließt --
        # beim Jahreswechsel werden alle zwölf Monatswerte auf einmal ersetzt,
        # und TOTAL_INCREASING läse darin einen frischen Ertrag.
        #
        # Keine Diagnose-Kategorie, obwohl er das bis zum 2026-09-03 war: die
        # ist für Werte über das Gerät gedacht, nicht für Fachdaten. Der
        # Vorjahresertrag ist dieselbe Größe wie der des laufenden Jahres, und
        # der einzige Grund, ihn anzusehen, ist der Vergleich mit ihm.
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
    def native_value(self) -> StateType | date:
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
            wert = float(raw)
        except ValueError:
            return raw
        if (rest := self.entity_description.plus_message) is None:
            return wert
        # Fehlt das zweite Register, wird nichts gemeldet statt eines Wertes,
        # der bis zu 99 zu niedrig wäre: ein zu kleiner Zählerstand sähe wie
        # ein Rücksprung aus, eine Lücke von einem Zyklus nicht.
        roh = self.coordinator.value(self.entity_description.source, rest)
        if roh is None:
            return None
        try:
            return wert + float(roh)
        except ValueError:
            return None

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        if not self.entity_description.expose_raw:
            return None
        raw = self.raw_message
        if raw is None:
            return None
        return dict(zip(_MONTHS, raw.split(";"), strict=False))
