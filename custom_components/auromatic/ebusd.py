"""Asynchroner Client fuer die Kommandoschnittstelle von ebusd (Port 8888).

Bewusst kein MQTT: ebusd beantwortet Schreibbefehle auf diesem Weg synchron,
Fehler kommen also als Rueckgabewert zurueck statt im Nichts zu verschwinden.
"""

from __future__ import annotations

import asyncio
import logging

_LOGGER = logging.getLogger(__name__)

# Werte, die ebusd fuer "kein gueltiger Messwert" liefert. Sie muessen
# ausgefiltert werden, sonst landen leere Schreibnachrichten und Dekodierfehler
# als Entitaeten in Home Assistant.
_NO_VALUE_PREFIXES = ("no data stored", "ERR:")

# ebusd haengt an Fuehlerwerte ein Statusfeld: "68.69;ok" oder "-19.38;cutoff".
# Nur "ok" ist ein angeschlossener Fuehler -- bei "cutoff" waere der
# Zahlenwert reiner Muell und darf nicht als Messwert erscheinen.
VALID_SENSOR_STATES = frozenset({"ok"})


class EbusdError(Exception):
    """Fehler bei der Kommunikation mit ebusd."""


class EbusdCommandError(EbusdError):
    """ebusd hat den Befehl mit einer Fehlermeldung beantwortet."""


class EbusdClient:
    """Haelt eine persistente Verbindung zu ebusd und serialisiert Befehle."""

    def __init__(self, host: str, port: int, timeout: float = 10.0) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        """Verbindung schliessen und Ressourcen freigeben."""
        async with self._lock:
            await self._disconnect()

    async def _disconnect(self) -> None:
        writer, self._writer, self._reader = self._writer, None, None
        if writer is None:
            return
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, asyncio.TimeoutError):  # pragma: no cover - Aufraeumpfad
            pass

    async def _connect(self) -> None:
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port), self._timeout
            )
        except (OSError, asyncio.TimeoutError) as err:
            raise EbusdError(f"Verbindung zu {self._host}:{self._port} fehlgeschlagen: {err}") from err

    async def command(self, cmd: str) -> list[str]:
        """Einen Befehl senden und die Antwortzeilen zurueckgeben.

        ebusd schliesst jede Antwort mit einer Leerzeile ab. Bricht die
        Verbindung weg, wird genau einmal neu verbunden und wiederholt --
        ein Adapter-Neustart soll keine Entitaeten auf "unavailable" werfen.
        """
        async with self._lock:
            for attempt in (1, 2):
                if self._writer is None:
                    await self._connect()
                try:
                    return await self._roundtrip(cmd)
                except EbusdCommandError:
                    raise
                except (OSError, asyncio.TimeoutError, EbusdError) as err:
                    await self._disconnect()
                    if attempt == 2:
                        raise EbusdError(f"Befehl '{cmd}' fehlgeschlagen: {err}") from err
                    _LOGGER.debug("Verbindung verloren, neuer Versuch fuer '%s'", cmd)
            raise EbusdError("unerreichbar")  # pragma: no cover

    async def _roundtrip(self, cmd: str) -> list[str]:
        assert self._writer is not None and self._reader is not None
        self._writer.write(f"{cmd}\n".encode())
        await asyncio.wait_for(self._writer.drain(), self._timeout)

        lines: list[str] = []
        while True:
            raw = await asyncio.wait_for(self._reader.readline(), self._timeout)
            if not raw:
                raise EbusdError("Verbindung von ebusd geschlossen")
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                break
            lines.append(line)

        if len(lines) == 1 and lines[0].startswith("ERR:"):
            raise EbusdCommandError(lines[0])
        return lines

    async def version(self) -> str:
        """Versionsstring von ebusd -- dient auch als Verbindungstest."""
        lines = await self.command("info")
        for line in lines:
            if line.startswith("version:"):
                return line.split(":", 1)[1].strip()
        raise EbusdError("ebusd hat keine Version gemeldet")

    async def find(self, circuit: str) -> dict[str, str]:
        """Alle zwischengespeicherten Werte eines Kreises holen.

        Ein Roundtrip pro Kreis statt einer Leseanfrage je Register: 'find'
        liefert den Cache von ebusd, erzeugt also keinen zusaetzlichen
        Busverkehr. Der eBUS ist langsam, das ist der entscheidende Punkt.
        """
        values: dict[str, str] = {}
        for line in await self.command(f"find -c {circuit}"):
            name, _, value = line.partition(" = ")
            if not _:
                continue
            # Je nach ebusd-Aufruf steht der Circuit dem Namen voran.
            key = name.split()[-1]
            value = value.strip()
            if not value or value.startswith(_NO_VALUE_PREFIXES) or value.startswith("("):
                continue
            # Lese- und Schreibvariante heissen gleich; die Schreibvariante hat
            # nie einen Wert und wurde oben bereits aussortiert.
            values.setdefault(key, value)
        return values

    async def write(self, circuit: str, message: str, value: str) -> None:
        """Einen Wert ueber eine dedizierte Set*-Nachricht schreiben.

        Ausschliesslich Einzelfeld-Nachrichten verwenden. Die Sammelnachricht
        'Mode' enthaelt unter anderem die Estrichtrocknung und wird nie
        beschrieben.
        """
        await self.command(f"write -c {circuit} {message} {value}")


def parse_field(raw: str | None, index: int = 0, status_index: int | None = None) -> str | None:
    """Ein Feld aus einem ebusd-Wert herausloesen und auf Gueltigkeit pruefen.

    Mehrfeldrige Werte sind semikolongetrennt. Ein "-" steht fuer einen Zaehler
    ohne Inhalt, ein Statusfeld ungleich "ok" fuer einen fehlenden Fuehler --
    beides ergibt keinen Messwert, sondern None.
    """
    if raw is None:
        return None
    parts = raw.split(";")
    if status_index is not None:
        if status_index >= len(parts) or parts[status_index] not in VALID_SENSOR_STATES:
            return None
    if index >= len(parts):
        return None
    value = parts[index].strip()
    if not value or value == "-":
        return None
    return value
