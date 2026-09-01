"""Gemeinsame Basis aller Entitaeten: Geraetezuordnung und Verfuegbarkeit."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CIRCUITS, DOMAIN
from .coordinator import AuromaticCoordinator


@dataclass(frozen=True, kw_only=True)
class CircuitMixin:
    """Bindet eine Entitaet an einen Kreis und eine ebusd-Nachricht.

    Als Mixin ausgelegt, damit die Plattformen ihre echte Basisklasse behalten
    (SensorEntityDescription und Verwandte) und deren Felder nicht neu
    definiert werden muessen.
    """

    circuit: str
    message: str
    field: int = 0
    status_field: int | None = None


@dataclass(frozen=True, kw_only=True)
class CircuitDescription(EntityDescription, CircuitMixin):
    """Fuer Plattformen ohne eigene Beschreibungsklasse."""


class AuromaticEntity(CoordinatorEntity[AuromaticCoordinator]):
    """Bindet eine Entitaet an genau einen Bus-Teilnehmer."""

    _attr_has_entity_name = True
    entity_description: CircuitDescription

    def __init__(
        self,
        coordinator: AuromaticCoordinator,
        description: CircuitDescription,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{description.circuit}_{description.key}"

        circuit = CIRCUITS[description.circuit]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_{description.circuit}")},
            name=f"auroMATIC {circuit['name']}",
            manufacturer="Vaillant",
            model=f"auroMATIC 620/3 ({description.circuit} @ {circuit['address']})",
            via_device=(DOMAIN, entry_id),
        )

    @property
    def raw_message(self) -> str | None:
        """Der unzerlegte Wert der Nachricht."""
        return self.coordinator.message(
            self.entity_description.circuit, self.entity_description.message
        )

    @property
    def raw_value(self) -> str | None:
        """Der aufbereitete Rohwert dieser Entitaet."""
        return self.coordinator.value(
            self.entity_description.circuit,
            self.entity_description.message,
            self.entity_description.field,
            self.entity_description.status_field,
        )

    @property
    def available(self) -> bool:
        return super().available and self.raw_message is not None
