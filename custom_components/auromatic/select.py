"""Betriebsart der Kreise -- der eigentliche Steuerhebel."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import MODE_OPTIONS
from .coordinator import AuromaticConfigEntry
from .ebusd import EbusdError
from .entity import AuromaticEntity, CircuitMixin


@dataclass(frozen=True, kw_only=True)
class AuromaticSelectDescription(SelectEntityDescription, CircuitMixin):
    """Lese- und Schreibnachricht heissen beim Regler nicht gleich."""

    write_message: str


SELECTS: tuple[AuromaticSelectDescription, ...] = (
    AuromaticSelectDescription(
        key="mode", circuit="hc", message="OperatingMode",
        write_message="SetMode", name="Betriebsart",
    ),
    AuromaticSelectDescription(
        key="mode", circuit="mc", message="OperatingMode",
        write_message="SetMode", name="Betriebsart",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AuromaticConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        AuromaticSelect(coordinator, description, entry.entry_id)
        for description in SELECTS
        if coordinator.value(description.circuit, description.message) is not None
    )


class AuromaticSelect(AuromaticEntity, SelectEntity):
    """Schaltet einen Kreis zwischen Zeitprogramm, Dauerbetrieb und Aus."""

    entity_description: AuromaticSelectDescription
    _attr_options = MODE_OPTIONS

    @property
    def current_option(self) -> str | None:
        raw = self.raw_value
        # "disabled" ist ein gueltiger Reglerzustand, aber keine Auswahl --
        # in dem Fall lieber nichts anzeigen als eine Option vorzutaeuschen.
        return raw if raw in MODE_OPTIONS else None

    async def async_select_option(self, option: str) -> None:
        """Betriebsart ueber die Einzelfeld-Nachricht SetMode schreiben.

        Nie ueber die Sammelnachricht 'Mode': die enthaelt auch die Felder der
        Estrichtrocknung, und die will bei einer verlegten Fussbodenheizung
        niemand versehentlich setzen.
        """
        description = self.entity_description
        try:
            await self.coordinator.client.write(description.circuit, description.write_message, option)
        except EbusdError as err:
            raise HomeAssistantError(f"Betriebsart konnte nicht gesetzt werden: {err}") from err
        await self.coordinator.async_request_refresh()
