# auroMATIC 620/3 für Home Assistant

Custom-Integration für die Vaillant auroMATIC 620/3 (Gerätekennung `SOLSY`)
über [ebusd](https://github.com/john30/ebusd). Sie erzeugt aus den ebusd-Werten
richtige Home-Assistant-Entitäten mit Gerätestruktur — statt roher MQTT-Topics.

[![Über HACS installierbar](https://img.shields.io/badge/HACS-Benutzerdefiniert-41BDF5.svg)](https://hacs.xyz)

## Aufbau

```
Heizung ── eBUS ── Adapter Shield C6 ── ebusd ── diese Integration ── HA
                   ens:…:9999          :8888
```

Die Integration spricht die **Kommandoschnittstelle von ebusd** (Standard-Port
8888), nicht MQTT. Schreibbefehle werden dadurch synchron quittiert; Fehler
kommen als Rückgabewert statt im Nichts zu verschwinden.

## Voraussetzungen

- ebusd (getestet gegen 26.1) mit geladener `vaillant/*.solsy.*.csv`-Konfiguration
- Der eBUS-Adapter akzeptiert **nur einen Client**. ebusd ist dieser Client —
  es darf keine zweite Instanz und kein Testskript parallel verbunden sein.
- Home Assistant 2024.6 oder neuer (die Integration nutzt `entry.runtime_data`).

## Installation

### Über HACS

Die Integration steht nicht im Standardkatalog von HACS, das Repository muss
deshalb einmal von Hand angemeldet werden: *HACS → Menü oben rechts →
Benutzerdefinierte Repositories*, dort `https://github.com/luc-ass/ha-auromatic`
mit der Kategorie *Integration* eintragen. Danach erscheint „Vaillant
auroMATIC (ebusd)" in der HACS-Liste, lässt sich herunterladen und meldet
künftige Versionen selbst.

Nach der Installation Home Assistant neu starten.

### Von Hand

`custom_components/auromatic/` in das Konfigurationsverzeichnis von Home
Assistant kopieren, neu starten.

### Einrichten

*Einstellungen → Geräte & Dienste → Integration hinzufügen → Vaillant
auroMATIC*.

### Welche Adresse?

Abgefragt wird die Adresse des **ebusd-Dienstes**, nicht die des Adapters.

Läuft ebusd als Add-on, steckt es in einem eigenen Docker-Container im selben
Netz wie Home Assistant. Der Supervisor vergibt als Hostnamen den Add-on-Slug
mit Bindestrichen — genau das, was im Add-on-Terminal im Prompt steht:

```
root@2ad9b828-ebusd:/#
     └─────┬──────┘
       der Hostname          →  2ad9b828-ebusd, Port 8888
```

Der Einrichtungsdialog klopft vorher selbst an: er fragt den Supervisor nach
Add-ons mit „ebusd" im Namen, leitet daraus Hostnamen ab und probiert sie
zusammen mit einigen Standardnamen gleichzeitig durch. Antwortet einer, ist das
Feld schon ausgefüllt.

Echte Autodiscovery über Zeroconf gibt es nicht: ebusd nutzt mDNS nur, um den
eBUS-Adapter zu finden (`--device=mdns:…`), meldet seinen eigenen Kommandoport
aber nicht an.

## Entitäten

Pro Bus-Adresse entsteht ein eigenes Gerät, alle hängen per `via_device` am
Regler. Die Entitäten sitzen dort, wo die Anlage sie hat -- nicht zwingend
dort, wo ebusd sie führt: was der ganzen Anlage gehört, liegt am Regler selbst,
und der Solarertrag steht beim Solar, obwohl ihn das Bedienteil zählt.

| Circuit | Adresse | Gerät | Wesentliche Entitäten |
|---|---|---|---|
| — | — | auroMATIC 620/3 | Außentemperatur, Sammelvorlauf und -rücklauf, Systemzustand, Störung, Ansteuerstunden |
| `bai` | 0x08 | Kessel | Wasserdruck, Flamme, Vorlauf und Rücklauf, Heizungspumpe, Wärmeanforderung des Reglers, Störung, Betriebsstunden und Schaltspiele |
| `ui` | 0x15 | Bedienteil | Raumfühler Heizungsraum |
| `cc` | 0x23 | Zirkulation | Betriebsart der Zirkulationspumpe, Pumpenzustand |
| `hwc` | 0x25 | Warmwasser | `water_heater` mit Speichertemperatur, Sollwert, Betriebsart |
| `hc` | 0x26 | Heizkreis | Betriebsart, Raumsollwerte, Heizkurve, Vorlaufsollwert |
| `mc` | 0x50 | Fußbodenheizung | Betriebsart, Raumsollwerte, Heizkurve, Vorlauftemperatur |
| `sc` | 0xec | Solar | Kollektor- und Speicherfühler, Solarertrag, Pumpenlaufzeit, Schaltdifferenzen, Schutz- und Auslegungswerte |

## Wie mit den Eigenheiten von ebusd umgegangen wird

Drei Fallstricke, die jeder naive Weg von ebusd nach Home Assistant trifft:

1. **Doppelte Nachrichtennamen.** Lese- und Schreibvariante heißen gleich
   (`FlowTempMax = 50` und `FlowTempMax = no data stored`). Die leere
   Schreibvariante wird verworfen, statt den gültigen Wert zu überschreiben.
2. **Nicht angeschlossene Fühler.** ebusd meldet `-19.38;cutoff`. Solche
   Register werden gar nicht erst zu Entitäten — sonst stünden −19 °C als
   Kollektortemperatur im Verlauf.
3. **Kaskadenregister.** Die Gerätedefinition kennt acht Kessel; bei einer
   Anlage mit einem liefern `B2`–`B8` Dekodierfehler und werden gefiltert.

## Schreibzugriffe

**Geschrieben wird auf dasselbe Register, das auch gelesen wird.**
`OperatingMode`, `TempDesired`, `TempDesiredLow` und `HeatingCurve` sind in der
ebusd-Konfiguration als `r;w` deklariert — ein Register, einmal lesend und
einmal schreibend, mit genau einem Feld.

Die naheliegenderen `Set*`-Nachrichten sind *nicht* der Weg, und das ist am
Gerät gelernt: es gibt sie nur im Mischerkreis, im Heizkreis antwortet ebusd
mit `ERR: element not found`, und ihr Datentyp `temp0` kennt nur ganze Grad,
während das Register selbst (`temp1`) 0,5 K auflöst. Geschriebene 21,5 °C kamen
als 21,0 zurück. Eine einzige begründete Ausnahme gibt es, `cc SetMode` — dem
Zirkulationskreis fehlt in der ebusd-Konfiguration ein einfeldriges
Leseregister für die Betriebsart. Sie steht mit Begründung in
`const.WRITE_EXCEPTIONS`, und `tests/test_translations.py` lässt genau diese
eine durch.

Die Sammelnachricht `Mode` wird **nur gelesen, nie geschrieben** — sie enthält
neben der Betriebsart auch `floorpavingdryingday` und `floorpavingdryingtemp`.
Ein Schreibvorgang darauf könnte die Estrichtrocknung starten und die
Fußbodenheizung tagelang hochfahren.

Nur lesend eingebunden sind außerdem, jeweils mit Grund: die
Vorlaufbegrenzungen (`FlowTempMax`, 40 °C am Mischerkreis) und die
Schutzwerte des Kollektorkreises — geräteseitige Absicherungen, an denen ein
Bedienelement zum Verstellen im Vorbeigehen einlüde. Und **am Kessel wird
überhaupt nichts geschrieben**: seine beschreibbaren Register liegen
ausnahmslos auf Installateur- und Serviceebene, in derselben Reihe wie
`SetFactoryValues` (d.96 Werkseinstellungen).

## Tests

```
python3 tests/test_ebusd.py         # Fake-ebusd mit echten Antworten
python3 tests/test_translations.py  # Entitäten gegen die HA-Richtlinien
```

Beide laufen ohne installiertes Home Assistant. `test_ebusd.py` spielt
wortgetreue `ebusctl`-Antworten der echten Anlage gegen einen Fake-ebusd;
`test_translations.py` hält die Entitätsbeschreibungen gegen `translations/`,
`icons.json` und den Poll-Satz — es fängt die Fehler ab, die sonst still
bleiben, etwa eine Entität ohne Namenseintrag oder ein Register, das gelesen,
aber nie bei ebusd angemeldet wird.

## Neue Version veröffentlichen

HACS bietet den Anwendern die GitHub-*Releases* an. Ein Release braucht deshalb
zweierlei, und zwar gleichlautend: das Tag (`v0.3.0`) und das Feld `version` in
`custom_components/auromatic/manifest.json` (`0.3.0`). Weichen sie voneinander
ab, lädt HACS zwar die Dateien, meldet aber weiter die alte Version als
installiert und bietet dasselbe Update immer wieder an.

## Stand

Lesender Teil, Betriebsartensteuerung, Sollwerte, Warmwasser, Zirkulation und
der Wärmeerzeuger sind gebaut. Noch offen: `climate`-Entitäten (brauchen verknüpfte Raumsensoren — das
eingebaute Bedienteil hängt im Heizungsraum und taugt nicht als Führungsgröße)
und die bedarfsgeführte Regelung der Fußbodenheizung über die Ventilstellungen
aus Homematic IP.
