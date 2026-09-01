"""Zentraler Abrufkoordinator für alle Kreise des Reglers."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CIRCUITS, DOMAIN
from .ebusd import EbusdClient, EbusdError, parse_field
from .poll import POLL_REGISTER_MAXAGE, POLL_SET

# Nach einem Neustart oder Rescan von ebusd sind die Definitionen für einige
# Minuten unvollständig. Lieber ein paar Mal nachfassen als eine Stunde lang
# mit halbem Poll-Satz laufen.
POLL_ATTEMPTS = 6
POLL_RETRY_DELAY = 30

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
        self._poll_task: asyncio.Task[None] | None = None

    async def _ensure_polled(self) -> None:
        """Prüfen, ob unsere Register noch in der Poll-Liste von ebusd stehen.

        Ein Rescan wirft sie stillschweigend heraus, und ohne Poll-Liste
        liefert 'find' bis in alle Ewigkeit denselben Wert -- ohne Fehler und
        ohne 'unavailable'. Das ist genau die Sorte Ausfall, die man wochenlang
        übersieht, deshalb wird sie jede Runde geprüft statt nach Zeitplan.
        Ein 'info' je Abruf, neben den sechs 'find' -- das fällt nicht ins
        Gewicht, und angemeldet wird nur, wenn tatsächlich etwas fehlt.
        """
        expected = sum(len(m) for m in POLL_SET.values())
        try:
            _, polled = await self.client.status()
        except EbusdError:
            return  # Der Abruf selbst meldet den Fehler gleich deutlicher.
        if polled >= expected:
            return
        _LOGGER.debug(
            "Poll-Liste von ebusd hat %d statt %d Einträge, Anmeldung wird erneuert",
            polled, expected,
        )
        await self.async_apply_poll()

    async def async_apply_poll(self) -> None:
        """ebusd sagen, welche Register es für uns aktiv abfragen soll.

        Muss wiederholt werden: die Poll-Liste lebt im Speicher von ebusd und
        ist nach dessen Neustart leer. Ohne Wiederholung liefe die Integration
        danach weiter, zeigte aber eingefrorene Werte -- ohne Fehlermeldung.

        Ein Durchlauf sofort, damit der Zwischenspeicher noch vor dem Anlegen
        der Entitäten gefüllt ist. Bleibt etwas offen, übernimmt eine Aufgabe
        im Hintergrund -- der Setup soll nicht minutenlang auf einen Rescan
        von ebusd warten.
        """
        if self._poll_task is not None and not self._poll_task.done():
            return
        if await self._apply_poll_once():
            return
        self._poll_task = self.config_entry.async_create_background_task(
            self.hass, self._retry_poll(), "auroMATIC: Poll-Satz nachreichen"
        )

    async def _retry_poll(self) -> None:
        for attempt in range(2, POLL_ATTEMPTS + 1):
            await asyncio.sleep(POLL_RETRY_DELAY)
            if await self._apply_poll_once(attempt):
                return
        _LOGGER.warning(
            "Poll-Satz nach %d Versuchen unvollständig. Die betroffenen Werte "
            "aktualisieren sich nicht mehr; sie bleiben stumm auf dem letzten "
            "bekannten Stand stehen.",
            POLL_ATTEMPTS,
        )

    async def _apply_poll_once(self, attempt: int = 1) -> bool:
        """Einen Anmeldedurchlauf. True, wenn nichts mehr offen ist.

        Einzelne Fehlschläge sind kein Grund aufzugeben: ein Register, das
        diese Anlage nicht kennt, darf die übrigen vierzig nicht mitreißen.
        """
        total = sum(len(m) for m in POLL_SET.values())
        try:
            scan_done, _ = await self.client.status()
        except EbusdError as err:
            _LOGGER.debug("Zustand von ebusd nicht lesbar: %s", err)
            return False
        if not scan_done:
            _LOGGER.debug("ebusd scannt noch, Poll-Anmeldung wird nachgeholt")
            return False

        failed: list[str] = []
        for circuit, messages in POLL_SET.items():
            for message, priority in messages.items():
                try:
                    await self.client.set_poll_priority(
                        circuit, message, priority, POLL_REGISTER_MAXAGE
                    )
                except EbusdError as err:
                    failed.append(f"{circuit} {message}: {err}")
        if failed:
            _LOGGER.debug(
                "Versuch %d: %d von %d Registern noch nicht anmeldbar: %s",
                attempt, len(failed), total, "; ".join(failed),
            )
            return False
        _LOGGER.debug("%d Register bei ebusd für den Abruf angemeldet", total)
        return True

    async def _async_update_data(self) -> dict[str, dict[str, str]]:
        await self._ensure_polled()

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

    async def async_write(
        self, circuit: str, write_message: str, value: str, read_message: str
    ) -> None:
        """Einen Wert schreiben und das Ergebnis sofort sichtbar machen.

        Ein bloßes async_request_refresh() genügt nicht: es liest den noch
        alten Cache von ebusd und wirft die Bedienung damit auf den vorherigen
        Wert zurück.
        """
        confirmed = await self.client.write_and_confirm(
            circuit, write_message, value, read_message
        )
        data = {name: dict(values) for name, values in (self.data or {}).items()}
        # Kam kein Lesewert zurück, gilt der geschriebene: ebusd hat den
        # Schreibvorgang quittiert. Der nächste Abruf korrigiert das ohnehin.
        data.setdefault(circuit, {})[read_message] = value if confirmed is None else confirmed
        self.async_set_updated_data(data)

    def message(self, circuit: str, message: str) -> str | None:
        """Den unzerlegten Wert einer Nachricht -- für Felder wie
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
