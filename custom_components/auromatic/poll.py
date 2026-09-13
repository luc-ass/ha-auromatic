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

Mit 19 Registern auf 1, fünf auf 3 und 29 auf 9 ergeben sich 23,89 Anteile:
ein Messwert kommt rund alle 2,4 Minuten an die Reihe, ein Bedienelement alle
7,2, ein Sollwert alle 21,5 -- bei unveränderter Buslast gegenüber den 156
Nachrichten von vorher.

Nachgemessen an einem vollen Tag (2026-09-02, 16 h Laufzeit, damals noch
21,11 Anteile): der Median zwischen zwei Messwerten lag bei **120 s**,
gerechnet waren 127. Das Modell stimmt also, und die Zahlen oben sind keine
Schätzung -- es rechnet eher etwas zu pessimistisch, weil fremder Verkehr auf
dem Bus einzelne Register frisch hält, die ebusd dann überspringt.

Der Kessel hat den Satz am 2026-09-04 von 18,89 auf 23,67 Anteile verbreitert
-- vier Register auf Stufe 1 und sieben auf Stufe 9 --, ein Messwert kommt
seither rechnerisch alle 142 statt alle 113 Sekunden. Das ist der Preis, und
er ist bewusst bezahlt: ohne Wasserdruck, Flamme und Fehlerspeicher des
Brenners bleibt genau die Störung unsichtbar, die diese Anlage am
2026-09-04 hatte (F.75, festsitzende Pumpe). Eines der vier auf Stufe 1
kostet dabei vermutlich gar nichts: `bai Status01` fragt das Bedienteil
ohnehin alle 17 Sekunden ab, und was frisch im Zwischenspeicher liegt,
überspringt der Poll -- derselbe Effekt wie bei `hc SumFlowSensor`.

Am 2026-09-13 kamen zwei Register auf Stufe 9 dazu (`bai HcUnderHundredStarts`
und `ui ServicePeriod`), von 23,67 auf 23,89 Anteile: ein Messwert kommt
statt alle 142 nun alle 143 Sekunden dran. Eine Sekunde für einen Zähler, der
sich sonst nur alle hundert Brennerstarts rührt, und für den Wartungstermin.

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
#
# Nur die Ertragsstatistik steht hier, und das mit Absicht: `read -m` ist der
# teurere der beiden Wege. Ein Register in der Warteschlange kostet keine
# einzige zusätzliche Anfrage -- ebusd pollt eine Nachricht je Takt, ob die
# Liste nun dreißig Einträge hat oder vierzig. Ein `read -m` dagegen geht nach
# Ablauf des Höchstalters *zusätzlich* auf den Bus. Für Konstanten lohnt sich
# das nicht; die gehören auf Stufe 9 in die Warteschlange, wo sie nichts
# kosten außer ein paar Sekunden Latenz für die übrigen.
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
    # Die Wärmeanforderung, die der Regler an den Kessel schickt. In
    # `hcmode.inc` steht sie als `uw` -- passiv mitgelesen und schreibend,
    # aber ohne Lesevariante. ebusd nimmt eine Poll-Anmeldung darauf zwar an
    # (am 2026-09-04 mit `read -p 1 -m 3600 -c bai SetMode` versucht,
    # quittiert mit dem Wert aus dem Zwischenspeicher), sendet dafür aber
    # keine eigene Anfrage; in der Busaufnahme steht danach kein einziges
    # `3108b510`-Telegramm. Der Eintrag wäre also bestenfalls wirkungslos --
    # und schlimmstenfalls ein Schreibtelegramm an den Brenner, denn der
    # Master-Teil dieser Nachricht *ist* der Stellbefehl. Aus demselben Grund
    # wie Invariante 1: eine Nachricht, die nie aktiv aufgerufen wird, kann
    # auch nichts verstellen.
    #
    # Frisch bleibt der Wert trotzdem: das Bedienteil schickt ihn alle 17
    # Sekunden, ebusd schneidet mit (`update: 12`).
    #
    # Eine Einschränkung hat das, und sie betrifft nur dieses eine Register:
    # an ihm hängen zwei Entitäten (`flow_desired` in sensor.py, `heat_release`
    # in binary_sensor.py), und `async_warm_cache` kann sie nicht absichern --
    # es läuft über POLL_SET und READ_MAXAGE, und hier darf nicht gelesen
    # werden. Startet ebusd kurz vor Home Assistant, ist noch kein
    # `1008b510`-Telegramm mitgeschnitten; `find` liefert die Nachricht dann
    # nicht, und die beiden Entitäten entstehen erst beim nächsten Start von
    # Home Assistant. Die übrigen ausgenommenen Register tragen keine Entität,
    # dort fällt es nicht ins Gewicht. Ein weiteres mit Entität gehört deshalb
    # nicht hierher, sondern in READ_MAXAGE.
    ("bai", "SetMode"): "nur passiv/schreibend definiert (uw), ebusd hört das Bedienteil mit",
}

# Kreis -> Nachricht -> Priorität.
POLL_SET: Final[dict[str, dict[str, int]]] = {
    # Der Wärmeerzeuger (0x08). Er ist seit dem 2026-09-04 wieder am Bus, und
    # der Anlass ist zugleich die Begründung für diesen Block: der Brenner
    # meldete an jenem Vormittag F.75 -- kein Druckanstieg beim Anlaufen der
    # Pumpe --, und die Pumpe musste von Hand gangbar gemacht werden. Sichtbar
    # war davon in Home Assistant nichts.
    "bai": {
        # Der Wert, um den es geht. Am 2026-09-04 stand er auf 1,46 bar; die
        # Ursache war also nicht Wassermangel, sondern die Pumpe. Beides
        # unterscheidet nur, wer den Druck über die Zeit sieht.
        "WaterPressure": POLL_MEASURED,
        # Der Fehlerspeicher des Brenners, nicht der des Reglers: `hc
        # Currenterror` trägt die Störungen der Regelung, `bai Currenterror`
        # die der Feuerungsautomatik. F.75 stand nur hier.
        "Currenterror": POLL_MEASURED,
        # Der Brenner läuft in Schüben von Minuten. Auf Stufe 9 (21 Minuten)
        # sähe man die meisten Zyklen gar nicht.
        "Flame": POLL_MEASURED,
        # Vorlauf, Rücklauf und Pumpenzustand in einer Nachricht -- drei
        # Entitäten für einen Platz. Das Bedienteil fragt sie ohnehin alle
        # 17 s ab; angemeldet ist sie trotzdem, denn sonst bliebe sie bei
        # abgeschaltetem Brenner stumm auf dem letzten Wert stehen, ohne dass
        # es auffiele. Genau der Ausfall, den dieses Modul beschreibt.
        "Status01": POLL_MEASURED,
        # Der Statuscode der Anzeige (S.xx). 31 heißt "kein Wärmebedarf".
        "Statenumber": POLL_SETTING,
        # Zählerstände. Sie ändern sich in Stunden, nicht in Minuten, und
        # stehen deshalb auf der billigsten Stufe. Die vier Pumpen- und
        # Heizzähler sind die Vorgeschichte zu jeder künftigen Störung;
        # `DeactivationsIFC` (ein einziger Zündfehler im ganzen Gerätleben)
        # ist der Gegenbeweis dafür, dass es am Brenner selbst liegt.
        "HcHours": POLL_SETTING,
        # Zwei Register, ein Zähler: `HcStarts` trägt den Faktor 100, die
        # beiden fehlenden Stellen stehen in `HcUnderHundredStarts`. Der Rest
        # gehört auf dieselbe Stufe wie der Hunderter -- kämen sie
        # verschieden oft, stünde die Summe öfter schief als nötig.
        "HcStarts": POLL_SETTING,
        "HcUnderHundredStarts": POLL_SETTING,
        "PumpHours": POLL_SETTING,
        "HcPumpStarts": POLL_SETTING,
        "HoursTillService": POLL_SETTING,
        "DeactivationsIFC": POLL_SETTING,
        # `Errorhistory` fehlt hier mit Absicht: die Nachricht trägt ein Feld
        # im Master-Teil (den Index des Eintrags) und lässt sich deshalb nicht
        # anmelden -- `read -p 9 -c bai Errorhistory` antwortet mit
        # "ERR: end of input reached". Ohne Poll fröre der Wert auf dem Stand
        # des letzten Abrufs ein, und eine Entität, die eine alte Störung als
        # aktuelle zeigt, ist schlimmer als keine. Nachzusehen ist die
        # Historie mit `ebusctl read -c bai -i 0 Errorhistory`; am 2026-09-04
        # stand dort `1;-:-;-.-.-;75`.
    },
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
        # Der Zustand der Zirkulationspumpe. Er gehört zum Gerät
        # "Zirkulation", das Register steht aber im Warmwasserkreis. Auf
        # Stufe 1, weil eine Pumpe in Minuten schaltet -- und weil sie sonst
        # der einzige Messwert des Kreises wäre, der nachhinkt.
        "CirPump2": POLL_MEASURED,
    },
    # Der Zirkulationskreis (0x23). Eine einzige Nachricht trägt alles, was
    # gebraucht wird: Betriebsart (Feld 2) und Reglerzustand. Auf Stufe 3 wie
    # die übrigen Bedienelemente -- geändert wird sie aus Home Assistant
    # heraus, wo `write_and_confirm` sie ohnehin sofort zurückliest; die
    # Warteschlange zählt nur, wenn jemand am Regler selbst dreht.
    "cc": {
        "Mode": POLL_CONTROL,
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
        # Eine Einstellung, kein Zustand. `SolProtection` stand vom
        # 2026-09-01 bis 2026-09-02 durchgehend auf `on` -- über 27 Stunden,
        # bei Kollektortemperaturen von 14 °C nachts bis 89 °C mittags, gegen
        # eine Schutzschwelle von 130 °C (`SolProtectionStartTemp`). Es meldet
        # also die freigegebene Funktion, nicht deren Auslösung.
        #
        # `TeleSwitch` stand hier bis zum 2026-09-03 daneben. An dieser Anlage
        # ist kein Telefonschalter angeschlossen und keiner geplant; der
        # Binärsensor zeigte damit eine Funktion, die es nicht gibt.
        "SolProtection": POLL_SETTING,
        "SolEnableDiffTemp1": POLL_SETTING,
        "SolDisableDiffTemp1": POLL_SETTING,
        "FrostProtectionEnabled": POLL_SETTING,
        "CollPumpHRuntime1": POLL_SETTING,
        # Die Schutz- und Auslegungswerte des Kollektorkreises. Sie ändern sich
        # nicht von selbst -- nur jemand am Regler verstellt sie --, und
        # deshalb stehen sie auf derselben Stufe wie die beiden Schaltdifferenzen
        # darüber. Fünf Einträge mehr auf Stufe 9 hoben die Anteile am
        # 2026-09-02 von 17,11 auf 17,67: ein Messwert kam statt alle 103 nun
        # alle 106 Sekunden dran. Drei Sekunden für fünf Werte, die man sonst
        # am Gerät ablesen müsste.
        #
        # `SolProtectionStartTemp` (130 °C) ist die Schwelle, ab der die
        # Kollektorpumpe zum Schutz abschaltet, `ScProtectionHysteresis` (30 K)
        # die Abkühlung, die sie wieder freigibt -- die aufgelöste CSV nennt
        # beide Werte im Kommentar zu `SolProtection` ausdrücklich zusammen.
        "SolProtectionStartTemp": POLL_SETTING,
        "ScProtectionHysteresis": POLL_SETTING,
        "SolHwcMaxLoadTemp1": POLL_SETTING,
        "KolTempMin1": POLL_SETTING,
        "SolFlowRate": POLL_SETTING,
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
        # Der Wartungstermin des Reglers. Er ändert sich einmal im Jahr, und
        # trotzdem steht er in der Warteschlange statt in READ_MAXAGE: ein
        # Platz auf Stufe 9 kostet keine einzige zusätzliche Anfrage, ein
        # `read -m` dagegen schon. Genau die Rechnung, die oben für die
        # Konstanten steht.
        "ServicePeriod": POLL_SETTING,
        # Die Ertragsstatistik steht in READ_MAXAGE, nicht hier.
    },
}
