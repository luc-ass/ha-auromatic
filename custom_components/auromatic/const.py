"""Konstanten und Kreis-Definitionen fuer die auroMATIC-Integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "auromatic"

CONF_CIRCUITS: Final = "circuits"

DEFAULT_PORT: Final = 8888
DEFAULT_SCAN_INTERVAL: Final = 60

# Die Kreise des Reglers. ebusd leitet den Circuit-Namen aus dem Dateinamen der
# geladenen CSV ab (26.solsy.hc.csv -> "hc"). Die Bus-Adresse dient nur zur
# Anzeige und zur eindeutigen Geraete-Identifikation.
CIRCUITS: Final[dict[str, dict[str, str]]] = {
    "ui": {"address": "0x15", "name": "Bedienteil"},
    "cc": {"address": "0x23", "name": "Zentralteil"},
    "hwc": {"address": "0x25", "name": "Warmwasser"},
    "hc": {"address": "0x26", "name": "Heizkreis"},
    "mc": {"address": "0x50", "name": "Fussbodenheizung"},
    "sc": {"address": "0xec", "name": "Solar"},
}

# Betriebsarten (Datentyp "mcmode" aus vaillant/_templates.tsp).
# "disabled" wird bewusst nicht zur Auswahl angeboten -- damit deaktiviert man
# den Kreis vollstaendig, das gehoert an den Regler und nicht in eine Automation.
MODE_OPTIONS: Final = ["auto", "on", "eco", "low", "off"]
