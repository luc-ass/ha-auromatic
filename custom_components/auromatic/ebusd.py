"""Asynchroner Client für die Kommandoschnittstelle von ebusd (Port 8888).

Bewusst kein MQTT: ebusd beantwortet Schreibbefehle auf diesem Weg synchron,
Fehler kommen also als Rückgabewert zurück statt im Nichts zu verschwinden.
"""

from __future__ import annotations

import asyncio
import logging

_LOGGER = logging.getLogger(__name__)

# Werte, die ebusd für "kein gültiger Messwert" liefert. Sie müssen
# ausgefiltert werden, sonst landen leere Schreibnachrichten und Dekodierfehler
# als Entitäten in Home Assistant.
_NO_VALUE_PREFIXES = ("no data stored", "ERR:")

# ebusd hängt an Fühlerwerte ein Statusfeld: "68.69;ok" oder "-19.38;cutoff".
# Nur "ok" ist ein angeschlossener Fühler -- bei "cutoff" wäre der
# Zahlenwert reiner Müll und darf nicht als Messwert erscheinen.
VALID_SENSOR_STATES = frozenset({"ok"})


class EbusdError(Exception):
    """Fehler bei der Kommunikation mit ebusd."""


class EbusdCommandError(EbusdError):
    """ebusd hat den Befehl mit einer Fehlermeldung beantwortet."""


class EbusdClient:
    """Hält eine persistente Verbindung zu ebusd und serialisiert Befehle."""

    def __init__(self, host: str, port: int, timeout: float = 10.0) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        """Verbindung schließen und Ressourcen freigeben."""
        async with self._lock:
            await self._disconnect()

    async def _disconnect(self) -> None:
        writer, self._writer, self._reader = self._writer, None, None
        if writer is None:
            return
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, asyncio.TimeoutError):  # pragma: no cover - Aufräumpfad
            pass

    async def _connect(self) -> None:
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port), self._timeout
            )
        except (OSError, asyncio.TimeoutError) as err:
            raise EbusdError(f"Verbindung zu {self._host}:{self._port} fehlgeschlagen: {err}") from err

    async def command(self, cmd: str) -> list[str]:
        """Einen Befehl senden und die Antwortzeilen zurückgeben.

        ebusd schließt jede Antwort mit einer Leerzeile ab. Bricht die
        Verbindung weg, wird genau einmal neu verbunden und wiederholt --
        ein Adapter-Neustart soll keine Entitäten auf "unavailable" werfen.
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
                    _LOGGER.debug("Verbindung verloren, neuer Versuch für '%s'", cmd)
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

    async def set_poll_priority(self, circuit: str, message: str, priority: int, maxage: int) -> None:
        """Eine Nachricht in die Poll-Liste von ebusd eintragen.

        Die Liste ist Laufzeitzustand von ebusd und steht in keiner CSV; ohne
        diesen Eintrag holt ebusd das Register nie wieder vom Bus und `find`
        liefert stumm den letzten bekannten Wert. Siehe poll.py.

        '-m' hält den Eintrag billig: liegt ein hinreichend junger Wert im
        Zwischenspeicher, antwortet ebusd daraus und der Bus bleibt unberührt.
        Nur beim allerersten Mal -- oder nach einem ebusd-Neustart -- kostet es
        einen Roundtrip, und der ist dann auch gewollt.
        """
        await self.command(f"read -p {priority} -m {maxage} -c {circuit} {message}")

    async def status(self) -> tuple[bool, int]:
        """(Scan abgeschlossen, Anzahl Nachrichten in der Poll-Liste).

        Beides steht in derselben Antwort auf 'info', beides wird gebraucht:

        *Scan-Zustand*, weil ebusd die CSV je Adresse erst beim Scannen lädt.
        Eine Poll-Anmeldung in diesem Fenster scheitert mit
        'element not found' für alles, was noch nicht an der Reihe war -- an
        der Anlage beobachtet: 0x15 und 0x25 waren geladen, 0x26 aufwärts
        nicht, und genau deren Register fielen aus.

        *Größe der Poll-Liste*, weil ein Rescan die betroffenen Nachrichten
        stillschweigend daraus entfernt (`MessageMap::remove` löscht das
        Nachrichtenobjekt und mit ihm den Listeneintrag). Die Liste ist damit
        das einzige verlässliche Signal dafür, dass neu angemeldet werden muss
        -- ein Zeitplan trifft den Zeitpunkt nie.
        """
        scan_done, polled = False, 0
        for line in await self.command("info"):
            key, _, value = line.partition(":")
            if key == "scan":
                scan_done = value.strip() == "finished"
            elif key == "poll":
                polled = int(value.strip() or 0)
        return scan_done, polled

    async def scan_result(self) -> dict[str, dict[str, str]]:
        """Was ebusd beim Scannen über die Teilnehmer erfahren hat.

        Ein Befehl für alle Adressen, und kein Buszugriff: 'scan result' gibt
        nur aus, was ohnehin gespeichert ist. Niemals 'scan' ohne 'result' --
        das stößt einen echten Scan an, und der wirft die Poll-Liste heraus
        (Invariante 6, am 2026-09-04 an der Anlage beobachtet).
        """
        return parse_scan(await self.command("scan result"))

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
        liefert den Cache von ebusd, erzeugt also keinen zusätzlichen
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
            # Lese- und Schreibvariante heißen gleich; die Schreibvariante hat
            # nie einen Wert und wurde oben bereits aussortiert.
            values.setdefault(key, value)
        return values

    async def read(self, circuit: str, message: str, maxage: int | None = None) -> str | None:
        """Eine einzelne Nachricht lesen -- am Cache vorbei oder aus ihm.

        Ohne 'maxage' mit '-f': der Cache wird umgangen, der Wert kommt frisch
        vom Bus. Das kostet einen Roundtrip auf dem langsamen Bus und ist
        deshalb nur nach einer Benutzeraktion vertretbar, niemals reihum im
        Abrufzyklus -- dort bleibt es bei einem 'find' je Kreis.

        Mit 'maxage' genau andersherum: ebusd antwortet aus dem
        Zwischenspeicher, solange der Wert jünger ist als die angegebene
        Sekundenzahl, und geht nur sonst auf den Bus. Damit lassen sich
        einzelne Register frisch halten, ohne sie in die Poll-Warteschlange zu
        stellen -- siehe READ_MAXAGE in poll.py.
        """
        cache = "-f" if maxage is None else f"-m {maxage}"
        lines = await self.command(f"read {cache} -c {circuit} {message}")
        value = lines[0].strip() if lines else ""
        if not value or value.startswith(_NO_VALUE_PREFIXES):
            return None
        return value

    async def write(self, circuit: str, message: str, value: str) -> None:
        """Einen Wert über eine dedizierte Set*-Nachricht schreiben.

        Ausschließlich Einzelfeld-Nachrichten verwenden. Die Sammelnachricht
        'Mode' enthält unter anderem die Estrichtrocknung und wird nie
        beschrieben.
        """
        await self.command(f"write -c {circuit} {message} {value}")

    async def write_and_confirm(
        self, circuit: str, write_message: str, value: str, read_message: str
    ) -> str | None:
        """Schreiben und den Lesewert unmittelbar danach frisch holen.

        'find' beantwortet ebusd aus dem Cache. Ein Schreibbefehl aktualisiert
        dort nur die Set*-Nachricht; die zugehörige Lesenachricht bleibt bis
        zum nächsten Poll auf dem alten Stand. Ohne dieses Nachlesen zeigt die
        Oberfläche direkt nach dem Schalten wieder den vorherigen Wert.

        Ein Schreibfehler wird durchgereicht, ein fehlgeschlagenes Nachlesen
        nicht: geschrieben wurde dann trotzdem.
        """
        await self.write(circuit, write_message, value)
        try:
            return await self.read(circuit, read_message)
        except EbusdError as err:
            _LOGGER.debug("Nachlesen von %s %s fehlgeschlagen: %s", circuit, read_message, err)
            return None


# Die Spalten einer Zeile aus 'scan result':
#   26;Vaillant;SOLSY;0500;6301;21;16;12;0020076588;0907;005114;N4
# Die ersten fünf kommen aus der Scan-Antwort selbst, die übrigen sieben aus
# der Nachricht 'Scan.<zz> Id' -- ihre Feldnamen stehen so in der
# ebusd-Definition. Aneinandergehängt ergeben die sieben genau die
# 28-stellige Seriennummer vom Typenschild (2+2+2+10+4+6+2).
_SCAN_KOPF = ("address", "manufacturer", "id", "sw", "hw")
_SCAN_SERIAL = ("prefix", "year", "week", "product", "supplier", "counter", "suffix")


def parse_scan(zeilen: list[str]) -> dict[str, dict[str, str]]:
    """Die Antwort von 'scan result' nach Busadresse aufschlüsseln.

    Nicht jeder Teilnehmer füllt alle Felder: der Brenner meldet
    ';;;;;;' als Kennung, seine Artikelnummer ist leer, und genau deshalb
    lädt ebusd seine Definition über den Zweig [Scan_id_product='']. Fehlt
    ein Feld, fehlt es -- erfunden wird nichts.
    """
    teilnehmer: dict[str, dict[str, str]] = {}
    for zeile in zeilen:
        felder = [f.strip() for f in zeile.split(";")]
        if len(felder) < len(_SCAN_KOPF):
            continue
        eintrag = {
            name: wert for name, wert in zip(_SCAN_KOPF, felder) if wert
        }
        adresse = eintrag.pop("address", "")
        if not adresse:
            continue
        rest = felder[len(_SCAN_KOPF):]
        if len(rest) == len(_SCAN_SERIAL) and all(rest):
            eintrag["serial"] = "".join(rest)
            eintrag["product"] = rest[_SCAN_SERIAL.index("product")]
            eintrag["built"] = f"KW {rest[2]}/20{rest[1]}"
        teilnehmer[adresse.lower()] = eintrag
    return teilnehmer


def hex_text(raw: str | None) -> str | None:
    """Ein HEX-Feld von ebusd als Text lesen.

    'bai SerialNumber' ist als HEX:8 definiert und kommt als Bytefolge
    '53 42 32 ...' -- darin steckt ASCII, hier 'SB206740'. Alles, was sich
    nicht als druckbarer Text lesen lässt, gilt als kein Wert.
    """
    if not raw:
        return None
    try:
        zeichen = bytes(int(b, 16) for b in raw.split())
    except ValueError:
        return None
    text = zeichen.decode("ascii", errors="ignore").strip()
    return text if text.isprintable() and text else None


def carry_forward(
    previous: dict[str, dict[str, str]],
    current: dict[str, dict[str, str]],
    missing_since: dict[tuple[str, str], float],
    now: float,
    limit: float,
) -> tuple[dict[tuple[str, str], float], list[tuple[str, str]]]:
    """Kurze Lücken im Zwischenspeicher von ebusd überbrücken.

    Eine mehrfeldrige Nachricht hat während ihres eigenen Lesevorgangs keinen
    Wert: ebusd holt jedes Feld mit einem eigenen Telegramm, und `find`
    liefert für die Nachricht in diesem Fenster nichts. Fällt ein Abruf genau
    hinein, verschwindet ein Register aus der Antwort und die Entität steht
    für einen Zyklus auf `unavailable` -- an der Anlage sechsmal in 16 Stunden
    beobachtet, jedes Mal an einem der beiden Ertragsregister.

    Deshalb gilt der vorherige Wert weiter, aber nur `limit` Sekunden lang.
    Die Frist ist der eigentliche Punkt: ein Register, das dauerhaft
    ausbleibt, *soll* `unavailable` werden. Genau das ist der Ausfall, den
    poll.py beschreibt -- steht ein Register nicht mehr in der Poll-Liste,
    liefert ebusd stumm den letzten bekannten Wert. Ein unbefristetes
    Weiterreichen würde diesen Ausfall verstecken.

    Ändert `current` an Ort und Stelle. Zurück kommen die noch überbrückten
    Register und die, deren Frist gerade abgelaufen ist -- letztere gehören
    ins Protokoll, sie sind der Übergang von "kurze Lücke" zu "weg".
    """
    still: dict[tuple[str, str], float] = {}
    expired: list[tuple[str, str]] = []
    for circuit, values in previous.items():
        for message, value in values.items():
            if message in current.get(circuit, {}):
                continue
            since = missing_since.get((circuit, message), now)
            if now - since > limit:
                expired.append((circuit, message))
                continue
            still[(circuit, message)] = since
            current.setdefault(circuit, {})[message] = value
    return still, expired


def sum_fields(raw: str | None) -> int | None:
    """Alle Felder einer mehrfeldrigen Nachricht addieren.

    Die Ertragsstatistik steht als zwölf Monatswerte in einer einzigen
    Nachricht ('26;38;157;...'), gefragt ist die Jahressumme. parse_field
    liefert hier nur das erste Feld -- also den Januar.
    """
    if raw is None:
        return None
    try:
        return sum(int(part) for part in raw.split(";"))
    except ValueError:
        return None


def parse_field(raw: str | None, index: int = 0, status_index: int | None = None) -> str | None:
    """Ein Feld aus einem ebusd-Wert herauslösen und auf Gültigkeit prüfen.

    Mehrfeldrige Werte sind semikolongetrennt. Ein "-" steht für einen Zähler
    ohne Inhalt, ein Statusfeld ungleich "ok" für einen fehlenden Fühler --
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
