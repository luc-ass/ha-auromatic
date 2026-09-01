"""Zustandsmeldungen: Stoerungen und Schalter des Reglers."""

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


def _has_error(raw: str) -> bool:
    """Der Fehlerspeicher fuehrt fuenf Codes. '-;-;-;-;-' heisst stoerungsfrei.

    Bewusst ueber den Gesamtwert statt ueber ein Einzelfeld: ein einzelnes '-'
    ist fuer sich genommen kein Messwert, die Kombination aber sehr wohl eine
    Aussage.
    """
    return any(part.strip() not in ("", "-") for part in raw.split(";"))


@dataclass(frozen=True, kw_only=True)
class AuromaticBinaryDescription(BinarySensorEntityDescription, CircuitMixin):
    """Beschreibung mit der Regel, wann der Zustand 'an' bedeutet."""

    is_on_fn: Callable[[str], bool] = _is_on


BINARY_SENSORS: tuple[AuromaticBinaryDescription, ...] = (
    AuromaticBinaryDescription(
        key="error", circuit="hc", message="Currenterror",
        name="Stoerung", device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=_has_error,
    ),
    AuromaticBinaryDescription(
        key="teleswitch", circuit="sc", message="TeleSwitch",
        name="Telefonschalter", entity_category=EntityCategory.DIAGNOSTIC,
    ),
    AuromaticBinaryDescription(
        key="frost_protection", circuit="sc", message="FrostProtectionEnabled",
        name="Frostschutz Solar", entity_category=EntityCategory.DIAGNOSTIC,
    ),
    AuromaticBinaryDescription(
        key="collector_protection", circuit="sc", message="SolProtection",
        name="Kollektorschutz", entity_category=EntityCategory.DIAGNOSTIC,
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
        if coordinator.message(description.circuit, description.message) is not None
    )


class AuromaticBinarySensor(AuromaticEntity, BinarySensorEntity):
    """Ein Zustand mit zwei Auspraegungen."""

    entity_description: AuromaticBinaryDescription

    @property
    def is_on(self) -> bool | None:
        raw = self.raw_message
        return None if raw is None else self.entity_description.is_on_fn(raw)
