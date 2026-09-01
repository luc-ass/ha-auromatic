"""Welche Register ebusd für uns aktiv vom Bus holen soll -- und wie oft.

ebusd hält einen Zwischenspeicher, aus dem `find` bedient wird. Gefüllt wird
der über eine Poll-Liste, die *kein* Bestandteil der CSV-Konfiguration ist: in
der gesamten Vaillant-Definition steht keine einzige Poll-Priorität, alle
Zeilen sind schlicht `r`. Die Liste ist reiner Laufzeitzustand, den jeder
Client per `read -p PRIO` setzt.

Bisher füllte sie der MQTT-Zweig von ebusd mit 156 Nachrichten -- ein Pfad, den
diese Integration gar nicht benutzt. Das kostet Frische: ebusd pollt

    eine Nachricht pro `--pollinterval` (Standard 5 s, real ~6 s am Bus)

unabhängig davon, wie lang die Liste ist. 156 Nachrichten heißen also rund
15,6 Minuten, bis ein bestimmtes Register wieder an der Reihe ist. Die
Buslast hängt allein am Takt, nie an der Länge der Liste -- ein kürzerer
Satz ist damit ohne jeden Mehrverkehr entsprechend schneller.

Die Liste ist eine Prioritätswarteschlange: nach jedem Abruf rückt eine
Nachricht um ihren Prioritätswert nach hinten. Niedrige Zahl heißt also
häufiger, und ein Register mit Priorität 9 kommt ein Neuntel so oft dran wie
eines mit Priorität 1.

Daraus die Zweiteilung unten:

* `POLL_MEASURED` -- Werte, die sich von selbst ändern. Sie bestimmen, was in
  der Oberfläche und in der Statistik als Verlauf ankommt.
* `POLL_SETTING` -- Sollwerte, Konfiguration und Zählerstände. Sie ändern sich
  nur, wenn jemand sie ändert, und dann liest `write_and_confirm` sie ohnehin
  sofort mit `read -f` frisch vom Bus. Sie brauchen die Warteschlange nur für
  den Fall, dass jemand direkt am Regler dreht.

Mit 21 Registern auf `POLL_MEASURED` und 20 auf `POLL_SETTING` kommt ein
Messwert rund alle 2,3 Minuten an die Reihe, ein Sollwert alle 21 -- bei
unveränderter Buslast gegenüber den 156 Nachrichten von vorher.

**Der Poll-Satz muss vollständig sein.** Steht ein Register hier nicht drin,
holt ebusd es nicht mehr vom Bus, und `find` liefert bis in alle Ewigkeit den
letzten bekannten Wert -- ohne Fehlermeldung, ohne `unavailable`. Genau das
prüft `tests/test_translations.py` gegen den tatsächlichen Registerbedarf der
Plattformen ab.

**Die Anmeldung ist kein einmaliger Vorgang.** Zwei Dinge werfen sie wieder
heraus, beide an der Anlage beobachtet:

* Während ebusd den Bus scannt, sind die Definitionen unvollständig -- die
  CSV wird je Adresse erst beim Scannen geladen. Eine Anmeldung in diesem
  Fenster scheitert für alles, was noch nicht an der Reihe war. Beim ersten
  Anlauf waren 0x15 und 0x25 geladen und 0x26 aufwärts nicht; genau deren
  32 Register fielen aus.
* Ein Rescan ersetzt die Nachrichtenobjekte und nimmt sie dabei still aus der
  Poll-Liste (`MessageMap::remove`). `scan` wechselt auf dieser Anlage immer
  wieder kurz auf `running`.

Deshalb prüft der Koordinator bei **jedem** Abruf, ob die Poll-Liste noch die
erwartete Größe hat, und meldet nur dann neu an. Ein Zeitplan trifft den
Zeitpunkt nie; die Länge der Liste ist das einzige verlässliche Signal.
"""

from __future__ import annotations

from typing import Final

# Priorität 1: so oft wie möglich. Priorität 9: ein Neuntel davon.
POLL_MEASURED: Final = 1
POLL_SETTING: Final = 9

# Ein Sollwert darf ruhig eine Weile alt sein, aber nicht beliebig: dieser
# Höchstwert für `read` sorgt dafür, dass die Anmeldung selbst keinen
# Busverkehr auslöst, solange der Zwischenspeicher etwas Brauchbares enthält.
POLL_REGISTER_MAXAGE: Final = 3600

# Register, die die Integration liest, die aber **nicht** gepollt werden
# können. `roomtempoffset.inc` definiert `RoomTempOffset` ausschließlich
# schreibend (`*w` / `w`), es gibt keine Lesevariante -- `read -p` darauf
# beantwortet ebusd mit "ERR: element not found". Einen Wert hat das Register
# trotzdem: ebusd hört den Schreibvorgang des Bedienteils passiv mit und legt
# ihn in den Zwischenspeicher. Der Wert ist damit so aktuell, wie der Regler
# ihn zuletzt gesetzt hat, und braucht keine Anfrage von uns.
POLL_PASSIVE: Final[frozenset[tuple[str, str]]] = frozenset({
    ("mc", "RoomTempOffset"),
})

# Kreis -> Nachricht -> Priorität.
POLL_SET: Final[dict[str, dict[str, int]]] = {
    "hc": {
        "OutsideTemp": POLL_MEASURED,
        "SumFlowSensor": POLL_MEASURED,
        "FlowTempDesired": POLL_MEASURED,
        # Eine auflaufende Störung soll nicht zwanzig Minuten brauchen.
        "Currenterror": POLL_MEASURED,
        "TempDesired": POLL_SETTING,
        "TempDesiredLow": POLL_SETTING,
        "HeatingCurve": POLL_SETTING,
        "OperatingMode": POLL_SETTING,
        "FlowTempMax": POLL_SETTING,
    },
    "mc": {
        "FlowTemp": POLL_MEASURED,
        "FlowTempDesired": POLL_MEASURED,
        "TempDesired": POLL_SETTING,
        "TempDesiredLow": POLL_SETTING,
        "HeatingCurve": POLL_SETTING,
        "OperatingMode": POLL_SETTING,
        "FlowTempMax": POLL_SETTING,
    },
    "hwc": {
        "Storage1Sensor2": POLL_MEASURED,
        "TempDesired2": POLL_SETTING,
        "OperatingMode2": POLL_SETTING,
    },
    "sc": {
        "Coll1Sensor": POLL_MEASURED,
        "Coll2Sensor": POLL_MEASURED,
        "Storage1Sensor3": POLL_MEASURED,
        "Storage2Sensor3": POLL_MEASURED,
        "Storage3Sensor3": POLL_MEASURED,
        "Storage4Sensor3": POLL_MEASURED,
        "SumBackflowSensor": POLL_MEASURED,
        "YieldSensor": POLL_MEASURED,
        # Die Kollektorpumpe schaltet in Minuten, nicht in Stunden.
        "SolCollPumpED1": POLL_MEASURED,
        "SolProtection": POLL_MEASURED,
        "TeleSwitch": POLL_MEASURED,
        "SolEnableDiffTemp1": POLL_SETTING,
        "SolDisableDiffTemp1": POLL_SETTING,
        "FrostProtectionEnabled": POLL_SETTING,
        "CollPumpHRuntime1": POLL_SETTING,
    },
    "ui": {
        "FlowTemp": POLL_MEASURED,
        "RoomTemp": POLL_MEASURED,
        "SystemModeStream1": POLL_MEASURED,
        "BoilerHoursB1": POLL_SETTING,
        "YieldThisYear": POLL_SETTING,
        "YieldLastYear": POLL_SETTING,
    },
}
