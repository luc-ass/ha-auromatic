"""Zustandsmeldungen: Störungen und Schalter des Reglers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ROOT_DEVICE
from .coordinator import AuromaticConfigEntry
from .entity import AuromaticEntity, CircuitMixin, async_add_available


def _is_on(raw: str) -> bool:
    """Standardfall: einfeldriger Schalter."""
    return raw.split(";")[0] == "on"


def _is_running(raw: str) -> bool:
    """Die Einschaltdauer der Kollektorpumpe ist an dieser Anlage kein
    Modulationsgrad: der Regler meldet 100 für ein und 0 für aus. Als
    Prozentsensor stand dort eine Kennzahl, die es gar nicht gibt.
    """
    return raw.split(";")[0].strip() not in ("", "-", "0")


def _boiler_pump_running(raw: str) -> bool:
    """`bai Status01` Feld 6: der Pumpenstatus des Kessels.

    Der Datentyp `pumpstate` kennt vier Werte (off, on, overrun, hwc), nicht
    nur zwei -- `overrun` ist der Nachlauf nach dem Brennerbetrieb, `hwc` die
    Speicherladung. Alles außer `off` heißt: die Pumpe dreht sich. Genau das
    ist die Frage, wenn der Brenner F.75 meldet.
    """
    parts = raw.split(";")
    return len(parts) > 5 and parts[5].strip() not in ("", "-", "off")


def _heat_released(raw: str) -> bool:
    """`bai SetMode` Feld 5: `disablehc` -- die Heizkreissperre.

    1 heißt gesperrt, 0 freigegeben. Gemeldet wird die Freigabe, weil das die
    Aussage ist, auf die es ankommt: liegt eine Anforderung an, dann darf der
    Brenner sie auch bedienen. Am 2026-09-04 stand das Feld über rund 70
    Minuten auf 0 -- der Testlauf, in dem die Störung auftrat.
    """
    parts = raw.split(";")
    return len(parts) > 4 and parts[4].strip() == "0"


def _has_error(raw: str) -> bool:
    """Der Fehlerspeicher führt fünf Codes. '-;-;-;-;-' heißt störungsfrei.

    Bewusst über den Gesamtwert statt über ein Einzelfeld: ein einzelnes '-'
    ist für sich genommen kein Messwert, die Kombination aber sehr wohl eine
    Aussage.
    """
    return any(part.strip() not in ("", "-") for part in raw.split(";"))


# Nur lesend -- alle Werte stammen aus einem Abruf des Koordinators.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class AuromaticBinaryDescription(BinarySensorEntityDescription, CircuitMixin):
    """Beschreibung mit der Regel, wann der Zustand 'an' bedeutet."""

    is_on_fn: Callable[[str], bool] = _is_on


BINARY_SENSORS: tuple[AuromaticBinaryDescription, ...] = (
    # --- Wärmeerzeuger (0x08) -----------------------------------------------
    AuromaticBinaryDescription(
        # Der Fehlerspeicher des Brenners, nicht der des Reglers: `hc
        # Currenterror` trägt die Störungen der Regelung, dieses Register die
        # der Feuerungsautomatik. Am 2026-09-04 stand F.75 nur hier -- der
        # Regler selbst meldete nichts.
        key="error", circuit="bai", message="Currenterror",
        device_class=BinarySensorDeviceClass.PROBLEM, is_on_fn=_has_error,
    ),
    AuromaticBinaryDescription(
        # Läuft der Brenner gerade. Kein device_class: HEAT hieße in Home
        # Assistant "heiß/kalt", und das ist etwas anderes als "Flamme an".
        key="flame", circuit="bai", message="Flame",
    ),
    AuromaticBinaryDescription(
        # Die interne Heizungspumpe des Kessels, aus `Status01` statt aus dem
        # eigenen Register `bai WP` (d.10) -- derselbe Zustand, aber ohne
        # zweiten Platz in der Warteschlange.
        #
        # Am Gerät heißt d.10 "Wasserpumpe". Der Oberflächenname weicht ab,
        # aus demselben Grund wie beim Sammelrücklauf: neben "Kollektorpumpe"
        # und "Zirkulationspumpe" sagt "Wasserpumpe" nicht, welche der drei
        # gemeint ist. Die aufgelöste CSV nennt sie im Kommentar selbst
        # "Interne Heizungspumpe".
        key="pump", translation_key="boiler_pump", circuit="bai",
        message="Status01", device_class=BinarySensorDeviceClass.RUNNING,
        is_on_fn=_boiler_pump_running,
    ),
    AuromaticBinaryDescription(
        # Ob der Regler den Heizbetrieb des Brenners freigegeben hat. Zusammen
        # mit dem Vorlaufsollwert, dem Pumpenzustand und dem Wasserdruck ergibt
        # das die Kette, an der ein F.75 ablesbar wird: Anforderung liegt an,
        # Freigabe erteilt, Pumpe soll laufen -- und der Druck rührt sich
        # nicht.
        key="heat_release", circuit="bai", message="SetMode",
        is_on_fn=_heat_released,
    ),
    AuromaticBinaryDescription(
        # Der Fehlerspeicher der Anlage, kein Kreiswert: dasselbe Register
        # steht unter jeder Adresse und trug am 2026-09-03 in `hc`, `cc` und
        # `sc` denselben Inhalt.
        key="error", circuit="hc", device_circuit=ROOT_DEVICE,
        message="Currenterror",
        device_class=BinarySensorDeviceClass.PROBLEM, is_on_fn=_has_error,
    ),
    AuromaticBinaryDescription(
        key="pump", circuit="sc", message="SolCollPumpED1",
        device_class=BinarySensorDeviceClass.RUNNING, is_on_fn=_is_running,
    ),
    AuromaticBinaryDescription(
        # Der Zustand des ZP-Ausgangs. Er steht im Warmwasserkreis, gehört aber
        # zum Gerät "Zirkulation" -- `cc Status0a` führt ihn zwar auch, kostet
        # aber einen zweiten Platz in der Warteschlange für denselben Wert.
        # Am 2026-09-03 gegengeprüft: `write -c cc SetMode on` schaltet beide
        # zeitgleich auf `on`.
        key="circulation_pump", circuit="cc", source_circuit="hwc",
        message="CirPump2", device_class=BinarySensorDeviceClass.RUNNING,
    ),
    AuromaticBinaryDescription(
        key="frost_protection", circuit="sc", message="FrostProtectionEnabled",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    AuromaticBinaryDescription(
        key="collector_protection", circuit="sc", message="SolProtection",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AuromaticConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_available(
        entry, async_add_entities, BINARY_SENSORS,
        lambda description: AuromaticBinarySensor(coordinator, description, entry.entry_id),
        # Die ganze Nachricht, nicht ein Feld daraus: `is_on_fn` bekommt sie
        # unzerlegt, weil manche Zustände erst aus mehreren Feldern entstehen.
        ready=lambda coord, description: coord.message(
            description.source, description.message
        ) is not None,
    )


class AuromaticBinarySensor(AuromaticEntity, BinarySensorEntity):
    """Ein Zustand mit zwei Ausprägungen."""

    entity_description: AuromaticBinaryDescription

    @property
    def is_on(self) -> bool | None:
        raw = self.raw_message
        return None if raw is None else self.entity_description.is_on_fn(raw)
