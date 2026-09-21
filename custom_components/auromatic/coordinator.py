"""Zentraler Abrufkoordinator für alle Kreise des Reglers."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CIRCUITS, DOMAIN
from .ebusd import (
    EbusdClient,
    EbusdCommandError,
    EbusdError,
    carry_forward,
    hex_text,
    parse_field,
)
from .poll import POLL_REGISTER_MAXAGE, POLL_SET, READ_MAXAGE

# Nach einem Neustart oder Rescan von ebusd sind die Definitionen für einige
# Minuten unvollständig. Lieber ein paar Mal nachfassen als eine Stunde lang
# mit halbem Poll-Satz laufen.
POLL_ATTEMPTS = 6
POLL_RETRY_DELAY = 30

# Wie lange ein Register fehlen darf, bevor die Entität es zugibt. Lang genug
# für die Sekunden, die ein mehrfeldriger Lesevorgang die Nachricht leer
# stehen lässt, kurz genug, um einen echten Ausfall nicht zu verstecken:
# selbst das trägste Register der Warteschlange kommt alle 15 Minuten dran.
CARRY_FORWARD_LIMIT = 600

# Wie viele Fehlversuche beim Aufwärmen des Zwischenspeichers hingenommen
# werden, bevor abgebrochen wird. Antwortet ebusd gar nicht -- etwa mitten in
# einem Scan --, scheitert jeder einzelne Versuch erst nach einem Zeitablauf,
# und 42 davon hintereinander legten den Setup minutenlang lahm. Gezählt wird
# deshalb nur, was tatsächlich Zeit kostet; eine Fehlerantwort von ebusd kommt
# sofort und ist kein Grund, den Rest der Liste stehen zu lassen.
WARM_CACHE_GIVE_UP = 3

# Wie lange nach einem Schreibvorgang gewartet wird, bevor das Register
# gelesen wird, das dessen Wirkung zeigt. Der Regler schaltet seine Ausgänge
# nicht im selben Moment, in dem er den Schreibbefehl quittiert: am 2026-09-03
# über beide Richtungen gemessen folgte `hwc CirPump2` dem `cc SetMode` nach
# rund 1,5 s (bei +1,44 s noch `off`, bei +1,59 s `on`; zurück bei +0,95 s noch
# `on`, bei +1,60 s `off`).
#
# Ohne diese Wartezeit ist das Nachlesen schlimmer als nutzlos: es holt den
# alten Wert frisch vom Bus und schreibt ihn damit in den Zwischenspeicher von
# ebusd -- wo er bis zum nächsten Durchlauf der Warteschlange stehen bleibt.
EFFECT_SETTLE = 2.0

# Ein Register aus READ_MAXAGE, das nicht antwortet, wird nicht eine ganze
# Stunde in Ruhe gelassen -- sonst hinge die Entität nach einer einzelnen
# gescheiterten Anfrage bis zum nächsten Termin in der Luft.
READ_RETRY_DELAY = 60

# Höchstalter für die unveränderlichen Gerätedaten. Sie ändern sich nie; das
# einzige Ziel ist, dass ebusd aus dem Zwischenspeicher antwortet statt auf den
# Bus zu gehen.
SCAN_MAXAGE = 86400

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
        # Kreise, deren CSV ebusd gerade nicht geladen hat. Der Kessel ist der
        # Fall, für den es die Liste gibt: ist er stromlos, kennt ebusd 'bai'
        # nicht, und jede Anmeldung darauf scheitert dauerhaft.
        self._unknown_circuits: set[str] = set()
        # Was ebusd beim Scannen über die Busteilnehmer erfahren hat, nach
        # Adresse ohne '0x'. Steht nach dem Setup fest und ändert sich nicht.
        self.participants: dict[str, dict[str, str]] = {}
        # Seit wann ein Register in der Antwort von ebusd fehlt.
        self._missing_since: dict[tuple[str, str], float] = {}
        # Kreise, die schon einmal einen Wert geliefert haben. Taucht einer neu
        # darin auf, sind seine Scan-Daten noch nicht geholt.
        self._answered: set[str] = set()
        # Die Register außerhalb der Warteschlange: wann das nächste Lesen
        # fällig ist, und was zuletzt herauskam (mit dem Zeitpunkt dazu).
        self._read_due: dict[tuple[str, str], float] = {}
        self._read_cache: dict[tuple[str, str], tuple[float, str]] = {}

    def _expected_polls(self) -> int:
        """Wie viele Einträge die Poll-Liste von ebusd haben müsste.

        Kreise, die ebusd nicht kennt, bleiben aus der Rechnung heraus. Sonst
        wäre der Vergleich in _ensure_polled bei stromlosem Kessel dauerhaft
        unerfüllbar: seine elf Anmeldungen scheitern jedes Mal, die Zahl bliebe
        immer elf zu klein, und der Koordinator liefe jede Minute in eine
        vollständige Neuanmeldung samt Wiederholungsaufgabe. Alle paar Minuten
        stünde dann "Poll-Satz unvollständig" im Protokoll -- und damit wäre
        die eine Warnung entwertet, die einen echten Verlust der Poll-Liste
        anzeigen soll.

        Die Liste pflegt _async_update_data: dort scheitert 'find' für einen
        unbekannten Kreis ohnehin, und sobald er antwortet, fällt er wieder
        heraus. Das Soll wächst damit von selbst wieder, und weil es dann über
        der tatsächlichen Zahl liegt, meldet der nächste Abruf den Kreis an.
        """
        return sum(
            len(messages)
            for circuit, messages in POLL_SET.items()
            if circuit not in self._unknown_circuits
        )

    async def _ensure_polled(self) -> None:
        """Prüfen, ob unsere Register noch in der Poll-Liste von ebusd stehen.

        Ein Rescan wirft sie stillschweigend heraus, und ohne Poll-Liste
        liefert 'find' bis in alle Ewigkeit denselben Wert -- ohne Fehler und
        ohne 'unavailable'. Das ist genau die Sorte Ausfall, die man wochenlang
        übersieht, deshalb wird sie jede Runde geprüft statt nach Zeitplan.
        Ein 'info' je Abruf, neben den sieben 'find' -- das fällt nicht ins
        Gewicht, und angemeldet wird nur, wenn tatsächlich etwas fehlt.
        """
        expected = self._expected_polls()
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
        diese Anlage nicht kennt, darf die übrigen nicht mitreißen.
        """
        total = self._expected_polls()
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
            if circuit in self._unknown_circuits:
                # Ohne geladene CSV nimmt ebusd keine Anmeldung an. Die elf
                # Fehlschläge des abwesenden Kessels dürfen den Durchlauf nicht
                # dauerhaft als unvollständig gelten lassen.
                continue
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

    async def async_warm_cache(self) -> None:
        """Fehlende Register einmal gezielt nachlesen -- vor den Entitäten.

        Eine Entität entsteht nur dort, wo ein Wert vorliegt. Das ist Absicht:
        ein nicht angeschlossener Fühler meldet `cutoff` und soll keine
        bekommen. Ein kalter Zwischenspeicher sieht aber genauso aus. Nach
        einem Neustart von ebusd ist die Poll-Liste leer; scheitert die
        Anmeldung für einzelne Register -- während eines Scans antwortet ebusd
        mit "element not found" --, fehlt deren Wert beim ersten Abruf.

        Seit `async_add_available` ist das kein dauerhafter Verlust mehr: die
        Entität entsteht, sobald ihr Register antwortet. Sie entsteht dann
        aber eben auch erst dann, und bis die Warteschlange von ebusd bei
        einem Register mit Priorität 9 vorbeikommt, vergehen bis zu 21
        Minuten. Dieser Schritt kauft die Zeit zurück.

        Am 2026-09-03 an der Anlage beobachtet: nach einem gemeinsamen Neustart
        von ebusd und Home Assistant fehlten 15 Entitäten, während ebusd für
        jedes der 42 Register einen gültigen Wert hatte.

        `read -m` kostet nichts, solange der Zwischenspeicher etwas hinreichend
        Junges enthält; erst sonst geht es auf den Bus, und genau das ist hier
        gewollt. Im Normalfall fehlt nichts und die Schleife tut nichts.

        Erfasst sind POLL_SET und READ_MAXAGE, also alles, was gelesen werden
        darf. Draußen bleibt `bai SetMode`: dessen Master-Teil ist der
        Stellbefehl an den Brenner, es wird deshalb nie aktiv aufgerufen. Die
        zwei Entitäten daran haben hier kein Netz -- die Begründung steht bei
        POLL_EXEMPT in poll.py.
        """
        data = {circuit: dict(values) for circuit, values in (self.data or {}).items()}
        fehlend = [
            (circuit, message, POLL_REGISTER_MAXAGE)
            for circuit, messages in POLL_SET.items()
            for message in messages
            if message not in data.get(circuit, {})
        ] + [
            (circuit, message, maxage)
            for (circuit, message), maxage in READ_MAXAGE.items()
            if message not in data.get(circuit, {})
        ]
        if not fehlend:
            return

        _LOGGER.info(
            "%d Register fehlen nach dem ersten Abruf und werden einzeln "
            "nachgelesen, damit ihre Entitäten entstehen", len(fehlend),
        )
        fehlversuche = 0
        for circuit, message, maxage in fehlend:
            try:
                value = await self.client.read(circuit, message, maxage)
                fehlversuche = 0
            except EbusdCommandError as err:
                # Keine Verzögerung, sondern eine sofortige Antwort: dieses
                # Register gibt es hier nicht. Das darf nicht aufs Aufgeben
                # zählen -- sonst bricht ein stromloser Kessel, dessen elf
                # Register alle in Millisekunden abgelehnt werden, das
                # Aufwärmen für jeden danach folgenden Kreis ab. Genau die
                # Entitäten fehlten dann, für die es diesen Schritt gibt.
                _LOGGER.debug("%s %s nicht nachlesbar: %s", circuit, message, err)
                continue
            except EbusdError as err:
                _LOGGER.debug("%s %s nicht nachlesbar: %s", circuit, message, err)
                fehlversuche += 1
                if fehlversuche >= WARM_CACHE_GIVE_UP:
                    _LOGGER.warning(
                        "Nachlesen abgebrochen, ebusd antwortet nicht. Die "
                        "betroffenen Entitäten entstehen erst beim nächsten "
                        "Start von Home Assistant.",
                    )
                    break
                continue
            if value is None:
                continue
            data.setdefault(circuit, {})[message] = value
            if (circuit, message) in READ_MAXAGE:
                # Buchführung von _read_outside_queue mitziehen, sonst gilt der
                # Wert dort weiter als ungelesen.
                now = time.monotonic()
                self._read_cache[(circuit, message)] = (now, value)
                self._read_due[(circuit, message)] = now + maxage

        self._answered |= {circuit for circuit, values in data.items() if values}
        self.async_set_updated_data(data)

    async def async_warn_silent_circuits(self) -> None:
        """Melden, welcher Kreis geladen ist und trotzdem nichts liefert.

        Zwei Fälle sehen im Abruf gleich aus und sind es nicht. Kennt ebusd
        den Kreis gar nicht, ist das kein Fehler: der Kessel war bis zum
        2026-09-04 stromlos, und während eines Scans ist jeder Kreis
        vorübergehend unbekannt. Hat ebusd die CSV dagegen geladen und der
        Kreis schweigt trotzdem, stimmt etwas nicht -- dann steht die
        Verdrahtung oder der Teilnehmer in Frage, und das gehört ins
        Protokoll statt in eine stille Lücke in der Oberfläche.

        Nur beim Setup. Später übernimmt `carry_forward` den Fall, dass ein
        Register verschwindet, und die Entitäten kommen von selbst nach,
        sobald ihr Kreis antwortet.
        """
        try:
            loaded = await self.client.loaded_circuits()
        except EbusdError as err:
            _LOGGER.debug("Geladene Kreise nicht lesbar: %s", err)
            return
        silent = sorted(
            circuit for circuit in CIRCUITS
            if circuit in loaded and not (self.data or {}).get(circuit)
        )
        if silent:
            _LOGGER.warning(
                "ebusd hat die Konfiguration für %s geladen, aber keines ihrer "
                "Register liefert einen Wert. Die zugehörigen Entitäten "
                "entstehen, sobald der Kreis antwortet.",
                ", ".join(silent),
            )

    async def async_read_participants(self) -> None:
        """Hersteller, Versionen und Seriennummern der Teilnehmer holen.

        Ein einziger Befehl für alle Adressen, und kein Telegramm auf dem Bus:
        'scan result' gibt aus, was ebusd beim Scannen ohnehin erfragt hat.

        Der Brenner ist der Sonderfall -- seine Kennung kommt leer zurück
        (';;;;;;'), er hat also weder Artikelnummer noch Seriennummer am Bus.
        Was er hat, ist 'bai SerialNumber': die Seriennummer seiner Elektronik,
        als HEX-Feld codiert. Die wird nur dort nachgelesen, wo die Scan-Daten
        nichts hergeben, mit großem Höchstalter -- der Wert ist unveränderlich,
        und ebusd beantwortet ihn aus dem Zwischenspeicher.
        """
        try:
            self.participants = await self.client.scan_result()
        except EbusdError as err:
            _LOGGER.debug("Scan-Daten nicht lesbar: %s", err)
            return

        for circuit, angaben in CIRCUITS.items():
            adresse = angaben["address"].removeprefix("0x").lower()
            eintrag = self.participants.get(adresse)
            if eintrag is None or eintrag.get("serial"):
                continue
            try:
                roh = await self.client.read(circuit, "SerialNumber", SCAN_MAXAGE)
            except EbusdError:
                continue
            if (text := hex_text(roh)) is not None:
                eintrag["board_serial"] = text

    def participant(self, circuit: str) -> dict[str, str]:
        """Die Scan-Daten zu einem Kreis, leer wenn es keine gibt."""
        adresse = CIRCUITS[circuit]["address"].removeprefix("0x").lower()
        return self.participants.get(adresse, {})

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
                #
                # Der Kreis bleibt dabei leer. Seine letzten Werte hier selbst
                # wieder einzusetzen wäre naheliegend und falsch: carry_forward
                # überspringt jedes Register, das bereits in der Antwort steht.
                # Die Frist aus CARRY_FORWARD_LIMIT käme nie zum Tragen, die
                # Werte stünden für immer still -- ohne 'unavailable', ohne
                # Warnung. Also genau der Ausfall, gegen den Invariante 6
                # geschrieben ist. Überbrückt wird in _bridge_gaps, befristet.
                errors.append(f"{circuit}: {err}")
                data[circuit] = {}
                # 'element not found' heißt: ebusd kennt den Kreis nicht, seine
                # CSV ist gar nicht geladen. Nur das nimmt ihn aus dem Soll des
                # Poll-Satzes -- ein Verbindungsfehler sagt darüber nichts.
                if isinstance(err, EbusdCommandError):
                    self._unknown_circuits.add(circuit)
            else:
                self._unknown_circuits.discard(circuit)

        if len(errors) == len(CIRCUITS):
            raise UpdateFailed("; ".join(errors))
        if errors:
            _LOGGER.debug("Kreise ohne Antwort: %s", "; ".join(errors))

        # Ein Kreis, der zum ersten Mal antwortet, bekommt gleich Entitäten --
        # die Plattformen legen sie an, sobald diese Daten stehen. Sie lesen
        # dabei Hersteller, Softwarestand und Seriennummer aus `participants`,
        # und zwar genau einmal, beim Anlegen des Geräts. Waren die Scan-Daten
        # beim Setup noch nicht da -- ebusd scannte noch --, bliebe das Gerät
        # für immer ohne diese Angaben. 'scan result' kostet kein Telegramm.
        answering = {circuit for circuit, values in data.items() if values}
        if self.data is None:
            self._answered = answering
        elif new := answering - self._answered:
            self._answered |= new
            _LOGGER.info(
                "Kreis %s antwortet erstmals, Gerätedaten werden nachgeholt",
                ", ".join(sorted(new)),
            )
            await self.async_read_participants()

        await self._read_outside_queue(data)
        self._bridge_gaps(data)
        return data

    async def _read_outside_queue(self, data: dict[str, dict[str, str]]) -> None:
        """Die Register frisch halten, die nicht in die Warteschlange gehören.

        `read -m` ist kein Buszugriff, solange der Zwischenspeicher etwas
        hinreichend Junges enthält -- ebusd antwortet dann daraus. Erst wenn
        der Wert zu alt ist, geht eine Anfrage auf den Bus, und genau das ist
        hier gewollt: einmal je Höchstalter statt reihum in der
        Warteschlange. Warum die Ertragsstatistik dort nichts zu suchen hat,
        steht in poll.py.

        Zwischen zwei Lesevorgängen liegen diese Register in keiner Antwort
        von ebusd -- `find` kennt sie nicht, weil sie nicht gepollt werden.
        Der Koordinator führt ihren Wert deshalb selbst weiter; die
        Überbrückung in `_bridge_gaps` ist dafür die falsche Stelle, sie ist
        auf Sekunden ausgelegt und nicht auf Stunden. Nach zwei versäumten
        Terminen gilt der Wert trotzdem als verloren.
        """
        now = time.monotonic()
        for key, maxage in READ_MAXAGE.items():
            circuit, message = key
            if now >= self._read_due.get(key, 0.0):
                try:
                    value = await self.client.read(circuit, message, maxage)
                except EbusdError as err:
                    _LOGGER.debug("%s %s nicht lesbar: %s", circuit, message, err)
                    value = None
                self._read_due[key] = now + (
                    maxage if value is not None else READ_RETRY_DELAY
                )
                if value is not None:
                    self._read_cache[key] = (now, value)

            read_at, cached = self._read_cache.get(key, (None, None))
            if read_at is None:
                continue
            if now - read_at > 2 * maxage:
                _LOGGER.warning(
                    "%s %s ist seit über %d Sekunden nicht mehr lesbar. Die "
                    "zugehörige Entität wird jetzt 'unavailable'.",
                    circuit, message, 2 * maxage,
                )
                del self._read_cache[key]
                continue
            data.setdefault(circuit, {})[message] = cached

    def _bridge_gaps(self, data: dict[str, dict[str, str]]) -> None:
        """Ein Register, das für einen Moment fehlt, nicht gleich abschreiben.

        Die Frist macht den Unterschied zwischen Überbrücken und Vertuschen --
        siehe carry_forward in ebusd.py.
        """
        self._missing_since, expired = carry_forward(
            self.data or {}, data, self._missing_since,
            time.monotonic(), CARRY_FORWARD_LIMIT,
        )
        for circuit, message in expired:
            _LOGGER.warning(
                "%s %s fehlt seit über %d Sekunden in der Antwort von ebusd. "
                "Die zugehörige Entität wird jetzt 'unavailable' -- entweder "
                "ist das Register aus der Poll-Liste gefallen oder der "
                "Teilnehmer antwortet nicht mehr.",
                circuit, message, CARRY_FORWARD_LIMIT,
            )

    async def async_write(
        self,
        circuit: str,
        write_message: str,
        value: str,
        read_message: str,
        effect: tuple[str, str] | None = None,
    ) -> None:
        """Einen Wert schreiben und das Ergebnis sofort sichtbar machen.

        Ein bloßes async_request_refresh() genügt nicht: es liest den noch
        alten Cache von ebusd und wirft die Bedienung damit auf den vorherigen
        Wert zurück.

        `effect` nennt ein zweites Register, das die *Wirkung* des
        Schreibvorgangs zeigt und deshalb mitgelesen wird. Bislang gibt es
        genau eines: der Zustand der Zirkulationspumpe steht nicht in dem
        Register, das die Betriebsart schaltet, sondern in `hwc CirPump2`.
        Ohne dieses Nachlesen wartet die Oberfläche darauf, dass die
        Warteschlange dort vorbeikommt -- am 2026-09-03 gemessene 82 Sekunden,
        rechnerisch bis zu 113. Wer gerade Dauerbetrieb eingeschaltet hat,
        liest in der Zeit "Pumpe: aus" und hält die Anlage für kaputt.

        Gelesen wird erst nach EFFECT_SETTLE: der Regler quittiert den
        Schreibbefehl, bevor er den Ausgang schaltet. Zu früh gelesen holt man
        den alten Wert -- und macht es damit schlimmer, statt es zu beheben.

        Ein Buszugriff je Benutzeraktion, nie reihum -- dieselbe Begründung wie
        beim Nachlesen der Lesenachricht selbst (Invariante 7).
        """
        confirmed = await self.client.write_and_confirm(
            circuit, write_message, value, read_message
        )
        data = {name: dict(values) for name, values in (self.data or {}).items()}
        # Kam kein Lesewert zurück, gilt der geschriebene: ebusd hat den
        # Schreibvorgang quittiert. Der nächste Abruf korrigiert das ohnehin.
        data.setdefault(circuit, {})[read_message] = value if confirmed is None else confirmed

        if effect is not None:
            effect_circuit, effect_message = effect
            await asyncio.sleep(EFFECT_SETTLE)
            try:
                wirkung = await self.client.read(effect_circuit, effect_message)
            except EbusdError as err:
                # Der Schreibvorgang selbst ist längst quittiert; ein
                # misslungenes Nachlesen der Wirkung darf ihn nicht zum
                # Fehlschlag machen. Der nächste Abruf holt den Wert ohnehin.
                _LOGGER.debug(
                    "Wirkung %s %s nicht lesbar: %s", effect_circuit, effect_message, err
                )
            else:
                if wirkung is not None:
                    data.setdefault(effect_circuit, {})[effect_message] = wirkung

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
