"""Schalter: Sonderfunktionen des Reglers, die von selbst wieder enden."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ONE_TIME_LOAD_OFF, ONE_TIME_LOAD_ON
from .coordinator import AuromaticConfigEntry
from .ebusd import EbusdError
from .entity import AuromaticEntity, CircuitMixin, async_add_available

# Schreibend: der eBUS ist langsam, Befehle laufen nacheinander.
PARALLEL_UPDATES = 1


def _loading(raw: str) -> bool:
    """`hwc Mode` Feld 6 (`daynight`): `load` während der einmaligen Ladung.

    Sonst steht dort `day` oder `night`, je nach Zeitfenster. Endet die Ladung
    von selbst, fällt das Feld zurück -- und der Schalter mit ihm.
    """
    return raw == "load"


@dataclass(frozen=True, kw_only=True)
class AuromaticSwitchDescription(SwitchEntityDescription, CircuitMixin):
    """Ein Schalter, der über feste Broadcast-Telegramme schaltet.

    Gelesen wird aus `message`/`field`, geschrieben gar nicht: die beiden
    Telegramme stehen in const.BROADCASTS, mit Nachweis.
    """

    telegram_on: str
    telegram_off: str
    is_on_fn: Callable[[str], bool]


SWITCHES: tuple[AuromaticSwitchDescription, ...] = (
    AuromaticSwitchDescription(
        # Die einmalige Speicherladung (Bedienungsanleitung 0020094390,
        # Kap. 5.13.3): lädt den Speicher einmal bis zum Sollwert, unabhängig
        # vom Zeitprogramm. Ein Schalter und kein Button, weil der Zustand
        # lesbar ist: er zeigt an, ob gerade geladen wird -- auch wenn die
        # Ladung am Regler selbst gestartet wurde --, und lässt sich abbrechen.
        #
        # `hwc Mode` ist die Sammelnachricht, die Invariante 1 vor dem
        # Schreiben schützt. Hier wird sie nur gelesen; geschaltet wird über
        # den Broadcast.
        key="one_time_load", circuit="hwc", message="Mode", field=5,
        telegram_on=ONE_TIME_LOAD_ON, telegram_off=ONE_TIME_LOAD_OFF,
        is_on_fn=_loading,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AuromaticConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_available(
        entry, async_add_entities, SWITCHES,
        lambda description: AuromaticSwitch(coordinator, description, entry.entry_id),
    )


class AuromaticSwitch(AuromaticEntity, SwitchEntity):
    """Startet oder bricht eine Sonderfunktion ab; der Zustand kommt vom Regler."""

    entity_description: AuromaticSwitchDescription

    @property
    def is_on(self) -> bool | None:
        raw = self.raw_value
        return None if raw is None else self.entity_description.is_on_fn(raw)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._send(self.entity_description.telegram_on)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send(self.entity_description.telegram_off)

    async def _send(self, telegram: str) -> None:
        description = self.entity_description
        try:
            await self.coordinator.async_broadcast(
                telegram, (description.circuit, description.message)
            )
        except EbusdError as err:
            raise HomeAssistantError(f"Befehl konnte nicht gesendet werden: {err}") from err
