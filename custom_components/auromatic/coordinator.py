"""Zentraler Abrufkoordinator fuer alle Kreise des Reglers."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CIRCUITS, DOMAIN
from .ebusd import EbusdClient, EbusdError, parse_field

_LOGGER = logging.getLogger(__name__)

type AuromaticConfigEntry = ConfigEntry[AuromaticCoordinator]


class AuromaticCoordinator(DataUpdateCoordinator[dict[str, dict[str, str]]]):
    """Fragt einmal pro Intervall alle Kreise ab und teilt das Ergebnis."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: AuromaticConfigEntry,
        client: EbusdClient,
        scan_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client

    async def _async_update_data(self) -> dict[str, dict[str, str]]:
        data: dict[str, dict[str, str]] = {}
        errors: list[str] = []

        for circuit in CIRCUITS:
            try:
                data[circuit] = await self.client.find(circuit)
            except EbusdError as err:
                # Ein einzelner stummer Kreis darf nicht die ganze Integration
                # abwerfen -- bei abgeschaltetem Brenner ist genau das normal.
                errors.append(f"{circuit}: {err}")
                data[circuit] = self.data.get(circuit, {}) if self.data else {}

        if len(errors) == len(CIRCUITS):
            raise UpdateFailed("; ".join(errors))
        if errors:
            _LOGGER.debug("Kreise ohne Antwort: %s", "; ".join(errors))
        return data

    def message(self, circuit: str, message: str) -> str | None:
        """Den unzerlegten Wert einer Nachricht -- fuer Felder wie
        'Currenterror = -;-;-;-;-', bei denen erst das Gesamtbild eine
        Bedeutung hat."""
        return (self.data or {}).get(circuit, {}).get(message)

    def value(
        self,
        circuit: str,
        message: str,
        index: int = 0,
        status_index: int | None = None,
    ) -> str | None:
        """Aufbereiteten Einzelwert holen, oder None wenn er nicht taugt."""
        return parse_field((self.data or {}).get(circuit, {}).get(message), index, status_index)
