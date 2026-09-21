"""Betriebsart der Kreise -- der eigentliche Steuerhebel."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import HWC_MODE_OPTIONS, MODE_OPTIONS
from .coordinator import AuromaticConfigEntry
from .ebusd import EbusdError
from .entity import AuromaticEntity, CircuitMixin, async_add_available


# Schreibend: der eBUS ist langsam, Befehle laufen nacheinander.
PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class AuromaticSelectDescription(SelectEntityDescription, CircuitMixin):
    """Betriebsart eines Kreises samt zugehöriger Schreibnachricht."""

    write_message: str
    # Register, das die Wirkung des Schreibvorgangs zeigt und deshalb
    # unmittelbar danach mitgelesen wird -- (Kreis, Nachricht). Siehe
    # AuromaticCoordinator.async_write.
    effect_message: tuple[str, str] | None = None


SELECTS: tuple[AuromaticSelectDescription, ...] = (
    AuromaticSelectDescription(
        key="mode", circuit="hc", message="OperatingMode",
        write_message="OperatingMode", options=[*MODE_OPTIONS],
    ),
    AuromaticSelectDescription(
        key="mode", circuit="mc", message="OperatingMode",
        write_message="OperatingMode", options=[*MODE_OPTIONS],
    ),
    # Der Zirkulationskreis geht andere Wege als die beiden Heizkreise: seine
    # Betriebsart steht nur im zweiten Feld der Sammelnachricht `Mode`, und
    # geschrieben wird sie über `SetMode`. Beides ist begründet in
    # const.WRITE_EXCEPTIONS und am Gerät nachgewiesen.
    #
    # Eigener Übersetzungsschlüssel, weil dieselben Rohwerte am Bedienteil
    # anders heißen: `on` ist hier "Ein", in den Heizkreisen "Heizen"
    # (Bedienungsanleitung 0020094390, Tab. 3.3 gegen Tab. 3.2). Der `key`
    # bleibt "mode" -- er steckt in der `unique_id`.
    AuromaticSelectDescription(
        key="mode", translation_key="circulation_mode",
        circuit="cc", message="Mode", field=1,
        write_message="SetMode", options=[*HWC_MODE_OPTIONS],
        # Der Pumpenzustand steht in einem anderen Kreis als die Betriebsart,
        # die ihn auslöst. Ohne Nachlesen bleibt die Pumpenkachel nach dem
        # Umschalten über eine Minute stehen.
        effect_message=("hwc", "CirPump2"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AuromaticConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_available(
        entry, async_add_entities, SELECTS,
        lambda description: AuromaticSelect(coordinator, description, entry.entry_id),
    )


class AuromaticSelect(AuromaticEntity, SelectEntity):
    """Schaltet einen Kreis zwischen Zeitprogramm, Dauerbetrieb und Aus."""

    entity_description: AuromaticSelectDescription

    @property
    def options(self) -> list[str]:
        """Nicht jeder Kreis kennt dieselben Stufen: der Zirkulationskreis
        führt seine Betriebsart im Datentyp `hwcmode` und hat weder `eco` noch
        `low`. Die Auswahl steht deshalb in der Beschreibung."""
        return list(self.entity_description.options or ())

    @property
    def current_option(self) -> str | None:
        raw = self.raw_value
        # "disabled" ist ein gültiger Reglerzustand, aber keine Auswahl --
        # in dem Fall lieber nichts anzeigen als eine Option vorzutäuschen.
        # Ablesbar bleibt er über den Diagnosesensor "mode_state".
        return raw if raw in self.options else None

    async def async_select_option(self, option: str) -> None:
        """Betriebsart über ein Register mit genau einem Feld schreiben.

        Nie über die Sammelnachricht 'Mode': die enthält auch die Felder der
        Estrichtrocknung, und die will bei einer verlegten Fußbodenheizung
        niemand versehentlich setzen. In den Heizkreisen ist 'OperatingMode'
        deshalb ein eigenes Register (PBSB 2B00) mit genau einem Feld, im
        Zirkulationskreis die einfeldrige Schreibnachricht 'SetMode' -- sie
        schreibt dasselbe Register 2B00, ebusd kennt dort nur keinen Lesenamen
        dafür. Gelesen wird in beiden Fällen aus 'message'; die Ausnahme steht
        mit Begründung in const.WRITE_EXCEPTIONS.
        """
        description = self.entity_description
        try:
            await self.coordinator.async_write(
                description.circuit, description.write_message, option,
                description.message, description.effect_message,
            )
        except EbusdError as err:
            raise HomeAssistantError(f"Betriebsart konnte nicht gesetzt werden: {err}") from err
