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

Daraus die drei Stufen unten:

* `POLL_MEASURED` (1) -- Werte, die sich von selbst ändern. Sie bestimmen, was
  in der Oberfläche und in der Statistik als Verlauf ankommt.
* `POLL_CONTROL` (3) -- Betriebsarten und der Warmwassersollwert. Aus Home
  Assistant heraus gesetzt, liest `write_and_confirm` sie ohnehin sofort mit
  `read -f` zurück; die Warteschlange zählt nur für den Fall, dass jemand am
  Regler selbst dreht. Zwanzig Minuten wären dafür zu träge, zwei zu teuer.
* `POLL_SETTING` (9) -- Sollwerte, Konfiguration und Zählerstände. Sie ändern
  sich selten, und niemand wartet auf sie.

Mit 14 Registern auf 1, vier auf 3 und 16 auf 9 ergeben sich 17,11 Anteile:
ein Messwert kommt rund alle 1,7 Minuten an die Reihe, ein Bedienelement alle
5, ein Sollwert alle 15 -- bei unveränderter Buslast gegenüber den 156
Nachrichten von vorher.

Nachgemessen an einem vollen Tag (2026-09-02, 16 h Laufzeit, damals noch
21,11 Anteile): der Median zwischen zwei Messwerten lag bei **120 s**,
gerechnet waren 127. Das Modell stimmt also, und die Zahlen oben sind keine
Schätzung.

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

**Der Satz kann zur Laufzeit nur wachsen.** ebusd nennt in `info` nur die
Größe der Liste, nicht ihren Inhalt, und kennt kein Kommando zum Abmelden.
Wird hier ein Register gestrichen, bleibt es in ebusd stehen, bis das Add-on
neu startet oder der nächste Rescan die Liste leert -- die Warteschlange ist
so lange noch die alte, und die übrigen Register werden erst danach
schneller. Ein Neustart von ebusd nach dem Kürzen erspart das Warten.
"""

from __future__ import annotations

from typing import Final

# Priorität 1: so oft wie möglich. Priorität 9: ein Neuntel davon.
POLL_MEASURED: Final = 1
# Bedienelemente in der Mitte: sie ändern sich nur, wenn jemand sie ändert --
# aber wenn das am Regler selbst passiert statt in Home Assistant, soll die
# Oberfläche nicht zwanzig Minuten hinterherhinken.
POLL_CONTROL: Final = 3
POLL_SETTING: Final = 9

# Ein Sollwert darf ruhig eine Weile alt sein, aber nicht beliebig: dieser
# Höchstwert für `read` sorgt dafür, dass die Anmeldung selbst keinen
# Busverkehr auslöst, solange der Zwischenspeicher etwas Brauchbares enthält.
POLL_REGISTER_MAXAGE: Final = 3600

# Register, die nicht in die Warteschlange gehören, aber trotzdem frisch
# bleiben müssen: der Koordinator holt sie selbst, höchstens einmal je
# Höchstalter, mit `read -m` statt `read -f`. Liegt ein jüngerer Wert im
# Zwischenspeicher, antwortet ebusd daraus und der Bus bleibt unberührt.
#
# Die Ertragsstatistik steht hier aus zwei Gründen. Erstens ist sie zwölf
# Felder breit, und ebusd holt jedes Feld mit einem eigenen Telegramm: in der
# Busaufnahme vom 2026-09-01 kam `YieldLastYear` auf 36 Anfragen -- drei
# Abrufe mal zwölf Monate --, beide Ertragsregister zusammen auf rund ein
# Fünftel des gesamten Verkehrs. Zweitens ändern sich die Werte genau einmal
# am Tag: am 2026-09-02 um 00:04 buchte der Regler die 6 kWh des Vortages,
# davor und danach stand die Zahl still.
#
# Dazu kam ein sichtbarer Fehler: während eines solchen mehrteiligen
# Lesevorgangs hat die Nachricht keinen Wert, `find` liefert für sie nichts,
# und die Entität fiel für einen Abrufzyklus auf `unavailable`. Am 2026-09-02
# sechsmal in 16 Stunden, jedes Mal exakt im 19-Minuten-Raster der Stufe 9.
# Stündlich statt alle 19 Minuten senkt die Trefferwahrscheinlichkeit; den
# Rest fängt die Überbrückung im Koordinator ab.
READ_MAXAGE: Final[dict[tuple[str, str], int]] = {
    ("ui", "YieldThisYear"): 3600,
    ("ui", "YieldLastYear"): 3600,
}

# Register, die die Integration zwar liest, die aber bewusst nicht gepollt
# werden -- je mit dem Grund. Jeder Eintrag hier kostet einen Platz in der
# Warteschlange weniger und macht damit alle übrigen schneller.
POLL_EXEMPT: Final[dict[tuple[str, str], str]] = {
    # Nur schreibend definiert (`*w` / `w` in roomtempoffset.inc), es gibt
    # keine Lesevariante -- `read -p` darauf beantwortet ebusd mit
    # "ERR: element not found". Einen Wert hat das Register trotzdem: ebusd
    # hört den Schreibvorgang des Bedienteils passiv mit.
    ("mc", "RoomTempOffset"): "nur schreibend definiert, ebusd hört passiv mit",
    # Diese Anlage hat ein Kollektorfeld und drei Speicherfühler. Beide
    # Register melden `cutoff`, es entsteht keine Entität -- sie würden einen
    # der schnellen Plätze für nichts belegen. Wird ein zweites Kollektorfeld
    # oder der dritte Speicherfühler angeschlossen, müssen sie hier raus.
    ("sc", "Coll2Sensor"): "nicht angeschlossen (cutoff)",
    ("sc", "Storage3Sensor3"): "nicht angeschlossen (cutoff)",
}

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
        "OperatingMode": POLL_CONTROL,
        "FlowTempMax": POLL_SETTING,
    },
    "mc": {
        "FlowTemp": POLL_MEASURED,
        "FlowTempDesired": POLL_MEASURED,
        "TempDesired": POLL_SETTING,
        "TempDesiredLow": POLL_SETTING,
        "HeatingCurve": POLL_SETTING,
        "OperatingMode": POLL_CONTROL,
        "FlowTempMax": POLL_SETTING,
    },
    "hwc": {
        "Storage1Sensor2": POLL_MEASURED,
        "TempDesired2": POLL_CONTROL,
        "OperatingMode2": POLL_CONTROL,
    },
    "sc": {
        "Coll1Sensor": POLL_MEASURED,
        # Speicherfühler 1 (oben) fehlt hier mit Absicht: `hwc Storage1Sensor2`
        # ist derselbe Fühler. Am 2026-09-02 über 140 zeitgleiche Messungen
        # verglichen, Abweichung höchstens 0,24 K und zu 90 % unter 0,1 K --
        # der Rest ist der Zeitversatz der beiden Abrufe. Die Entität im
        # Solarkreis liest deshalb über `source_circuit` aus dem Warmwasser.
        "Storage2Sensor3": POLL_MEASURED,
        "Storage4Sensor3": POLL_MEASURED,
        "SumBackflowSensor": POLL_MEASURED,
        "YieldSensor": POLL_MEASURED,
        # Die Kollektorpumpe schaltet in Minuten, nicht in Stunden.
        "SolCollPumpED1": POLL_MEASURED,
        # Beides sind Einstellungen, keine Zustände. `SolProtection` stand vom
        # 2026-09-01 bis 2026-09-02 durchgehend auf `on` -- über 27 Stunden,
        # bei Kollektortemperaturen von 14 °C nachts bis 89 °C mittags, gegen
        # eine Schutzschwelle von 130 °C (`SolProtectionStartTemp`). Es meldet
        # also die freigegebene Funktion, nicht deren Auslösung. `TeleSwitch`
        # blieb im selben Zeitraum unverändert; ein Telefonschalter ist an
        # dieser Anlage nicht angeschlossen, und wo einer hängt, handelt der
        # Regler ohnehin selbst -- die Anzeige darf nachlaufen.
        "SolProtection": POLL_SETTING,
        "TeleSwitch": POLL_SETTING,
        "SolEnableDiffTemp1": POLL_SETTING,
        "SolDisableDiffTemp1": POLL_SETTING,
        "FrostProtectionEnabled": POLL_SETTING,
        "CollPumpHRuntime1": POLL_SETTING,
    },
    "ui": {
        # `ui FlowTemp` fehlt hier mit Absicht: es ist derselbe Messwert wie
        # `hc SumFlowSensor` (identische Wertfolge, höchstens 0,63 K
        # Zeitversatz), und den hält das Bedienteil ohne unser Zutun frisch --
        # Median 30 s gegen 120 s bei allem, was wir selbst pollen. Zwei
        # Plätze in der Warteschlange für eine Temperatur wären zwei zu viel.
        "RoomTemp": POLL_SETTING,
        "SystemModeStream1": POLL_MEASURED,
        "BoilerHoursB1": POLL_SETTING,
        # Die Ertragsstatistik steht in READ_MAXAGE, nicht hier.
    },
}
