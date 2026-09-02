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

from .coordinator import AuromaticConfigEntry
from .entity import AuromaticEntity, CircuitMixin


def _is_on(raw: str) -> bool:
    """Standardfall: einfeldriger Schalter."""
    return raw.split(";")[0] == "on"


def _is_running(raw: str) -> bool:
    """Die Einschaltdauer der Kollektorpumpe ist an dieser Anlage kein
    Modulationsgrad: der Regler meldet 100 für ein und 0 für aus. Als
    Prozentsensor stand dort eine Kennzahl, die es gar nicht gibt.
    """
    return raw.split(";")[0].strip() not in ("", "-", "0")


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
    AuromaticBinaryDescription(
        key="error", circuit="hc", message="Currenterror",
        device_class=BinarySensorDeviceClass.PROBLEM, is_on_fn=_has_error,
    ),
    AuromaticBinaryDescription(
        key="pump", circuit="sc", message="SolCollPumpED1",
        device_class=BinarySensorDeviceClass.RUNNING, is_on_fn=_is_running,
    ),
    AuromaticBinaryDescription(
        key="teleswitch", circuit="sc", message="TeleSwitch",
        entity_category=EntityCategory.DIAGNOSTIC,
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
    async_add_entities(
        AuromaticBinarySensor(coordinator, description, entry.entry_id)
        for description in BINARY_SENSORS
        if coordinator.message(description.source, description.message) is not None
    )


class AuromaticBinarySensor(AuromaticEntity, BinarySensorEntity):
    """Ein Zustand mit zwei Ausprägungen."""

    entity_description: AuromaticBinaryDescription

    @property
    def is_on(self) -> bool | None:
        raw = self.raw_message
        return None if raw is None else self.entity_description.is_on_fn(raw)
