"""Konstanten und Kreis-Definitionen für die auroMATIC-Integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "auromatic"

CONF_CIRCUITS: Final = "circuits"

# Das Wurzelgerät -- der Regler selbst, an dem die Kreise per `via_device`
# hängen. Als `device_circuit` einer Beschreibung sammelt es die Werte, die
# keinem Kreis gehören, sondern der Anlage: Außentemperatur, Sammelvorlauf und
# -rücklauf, Systemzustand, Störung und die Ansteuerstunden. Sie stehen zwar in
# je einem Kreis, weil ebusd sie nur dort kennt -- der Regler beantwortet sie
# aber aus derselben Quelle. Am Gerät belegt: `hc OutsideTemp` und
# `ui OutsideTemp` lieferten am 2026-09-03 zeitgleich 17,81 °C, und
# `Currenterror` steht in `hc`, `cc` und `sc` auf demselben Wert.
ROOT_DEVICE: Final = "controller"

DEFAULT_PORT: Final = 8888
DEFAULT_SCAN_INTERVAL: Final = 60

# Die Kreise des Reglers. ebusd leitet den Circuit-Namen aus dem Dateinamen der
# geladenen CSV ab (26.solsy.hc.csv -> "hc"). Die Bus-Adresse dient nur zur
# Anzeige und zur eindeutigen Geräte-Identifikation.
#
# `model` und `device_name` stehen nur dort, wo der Teilnehmer kein auroMATIC
# ist: der Kessel ist ein eigenes Gerät am selben Bus, kein Kreis des Reglers.
# Alles andere leitet entity.py aus Name und Adresse ab.
CIRCUITS: Final[dict[str, dict[str, str]]] = {
    # Der Wärmeerzeuger (0x08). Er ist kein Kreis des Reglers, sondern ein
    # zweites Gerät am Bus -- ebusd lädt für ihn `vaillant/08.bai.csv` und
    # darüber `bai.0010006101.inc`. An dieser Anlage war er bis zum 2026-09-04
    # stromlos (Zustand vom Hausverkauf) und deshalb gar nicht vorhanden;
    # seither antwortet er, und der Kreis existiert. Bleibt er wieder aus,
    # fehlen seine Register schlicht -- der Koordinator verträgt einen stummen
    # Kreis, und die Entitäten entstehen beim Setup nur, wenn Werte vorliegen.
    "bai": {
        "address": "0x08",
        "name": "Kessel",
        # Ohne diese Ausnahme hieße das Gerät "auroMATIC Kessel" -- über einer
        # Modellzeile, die "Vaillant BAI00" nennt. Wer am Brenner steht, soll
        # dort dasselbe lesen wie in Home Assistant; er gehört nicht zum
        # Regler, sondern hängt neben ihm am Bus.
        # Wandhängendes Heizgerät ohne Warmwasserbereitung -- bei Vaillant
        # ein VC, umgangssprachlich eine Therme. Belegt am 2026-09-06:
        # `bai HwcTemp` meldet `circuit` (kein WW-Vorlauffühler),
        # `bai StorageTemp` meldet `cutoff` (kein geräteseitiger Speicher),
        # und der Benutzer hat das Gerät als wandhängend bestätigt.
        "device_name": "Therme",
        "model": "Vaillant BAI00 (bai @ 0x08)",
    },
    # Das Bedienteil ist ein eigener Artikel (0020080465) mit eigener
    # Seriennummer und eigenem Softwarestand, kein Kreis des Reglers -- und
    # trägt deshalb wie die Therme ein eigenes Modell.
    "ui": {
        "address": "0x15",
        "name": "Bedienteil",
        "model": "CI of VRS 620/3 (ui @ 0x15)",
    },
    "cc": {"address": "0x23", "name": "Zirkulation"},
    "hwc": {"address": "0x25", "name": "Warmwasser"},
    "hc": {"address": "0x26", "name": "Heizkreis"},
    "mc": {"address": "0x50", "name": "Fußbodenheizung"},
    "sc": {"address": "0xec", "name": "Solar"},
}

# Betriebsarten der Heizkreise (Datentyp "mcmode" aus vaillant/_templates.tsp).
# Die Reihenfolge ist die der Bedienungsanleitung 0020094390, Tab. 3.2
# ("Betriebsarten für Heizkreise"), die Beschriftungen in translations/ sind
# wortgleich mit dem Reglermenü: Auto, Heizen, Eco, Absenken, Aus. Wer sie
# ändert, baut eine Abweichung zwischen Home Assistant und dem Bedienteil --
# und die fällt genau dann auf, wenn ohnehin etwas klemmt.
#
# "disabled" wird bewusst nicht zur Auswahl angeboten -- damit deaktiviert man
# den Kreis vollständig, das gehört an den Regler und nicht in eine Automation.
MODE_OPTIONS: Final = ["auto", "on", "eco", "low", "off"]

# Warmwasser- und Zirkulationskreis teilen sich einen anderen, kürzeren Satz:
# Datentyp "hwcmode" (0=disabled;1=on;2=off;3=auto), und die Anleitung führt
# beide in derselben Tabelle 3.3 ("Betriebsarten für Zirkulationskreis und
# Warmwasserkreis") mit Auto, Ein und Aus. Weder "Eco" noch "Absenken" gibt es
# dort -- das Register `hwc OperatingMode2` ist in der ebusd-Konfiguration zwar
# als "mcmode" deklariert und nähme sie an, am Bedienteil erschiene aber
# etwas, das dessen Menü nicht kennt.
HWC_MODE_OPTIONS: Final = ["auto", "on", "off"]

# Invariante 2 verlangt, dass die Schreibnachricht so heißt wie die
# Lesenachricht -- die Register sind in der ebusd-Konfiguration als "r;w"
# deklariert, und die Set*-Nachrichten sind anderswo der falsche Weg. Hier
# stehen die begründeten Ausnahmen, je (Kreis, Lesename, Schreibname).
# tests/test_translations.py lässt genau diese durch und verlangt zu jeder
# einen Grund.
WRITE_EXCEPTIONS: Final[dict[tuple[str, str, str], str]] = {
    # Der Zirkulationskreis hat in der ebusd-Konfiguration kein einfeldriges
    # Betriebsart-Register: 23.solsy.cc.csv bindet nur hwcmode.inc ein, und
    # das liefert die Sammelnachricht `Mode` samt der Schreibnachricht
    # `SetMode`. Der Regler selbst führt das Register durchaus -- am
    # 2026-09-03 mit `hex 23b509030d2b00` gelesen, Antwort `0103` = auto --,
    # nur kennt ebusd dafür keine Definition, und eine eigene CSV wäre ein
    # Fork der Community-Konfiguration.
    #
    # `SetMode` ist hier trotzdem unbedenklich, anders als im Heizkreis: es
    # trägt genau ein Feld (Invariante 1 bleibt gewahrt) und hat denselben
    # Datentyp wie das gelesene Feld -- es geht also keine Auflösung verloren,
    # wie seinerzeit bei `SetTempDesired` (temp0 statt temp1). Am Gerät
    # nachgewiesen: nach `write -c cc SetMode on` steht das Rohregister 2B00
    # auf `0101`. `SetMode` schreibt genau das Register, das ein
    # `cc OperatingMode` schreiben würde.
    ("cc", "Mode", "SetMode"): "cc hat kein einfeldriges OperatingMode in der ebusd-Konfiguration",
}
