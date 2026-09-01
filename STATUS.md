# Stand der Umsetzung

Stand: 2026-09-01. Planungsdokument mit Herleitung und Registerkarte:
<https://claude.ai/code/artifact/e2491a1b-e82f-496d-95e7-3a4633908ec0>

---

## 1. Die Anlage

| | |
|---|---|
| Regler | Vaillant auroMATIC 620/3, Kennung `SOLSY`, SW 0500 / HW 6301, Ident 0020076588 |
| Bedienteil | `UI`, „CI of VRS 620/3", SW 0508 / HW 6201, Ident 0020080465 |
| Adapter | eBUS Adapter Shield C6, ESP32-C6, PCB 2.44.0, **10.23.10.114** |
| Adapter-Modus | `ens` (enhanced) über TCP **Port 9999 — nur ein Client gleichzeitig** |
| ebusd | 26.1, als Home-Assistant-Add-on, Hostname **`2ad9b828-ebusd`**, Port 8888 |
| Home Assistant | HA OS / Supervised |

### Bus-Teilnehmer

| Circuit | Adresse | Bedeutung | CSV |
|---|---|---|---|
| `ui` | 0x15 | Bedienteil, Systemebene | `vaillant/15.ui.csv` |
| `cc` | 0x23 | Zentralteil (nur lesend) | `vaillant/23.solsy.cc.csv` |
| `hwc` | 0x25 | Warmwasser | `vaillant/25.solsy.hwc.csv` |
| `hc` | 0x26 | Heizkreis (Radiatoren) | `vaillant/26.solsy.hc.csv` |
| `mc` | 0x50 | **Mischerkreis = Fußbodenheizung** | `vaillant/50.solsy.mc.csv` |
| `sc` | 0xec | Solarkreis | `vaillant/ec.solsy.sc.csv` |

Zusätzlich senden zwei Master `0x3f` und `0x7f`, deren Slaves `0x44` / `0x84`
keine Identifikation beantworten. Unbekannt, bislang unkritisch.

### Anlagenspezifische Besonderheiten

- **Kein Wärmeerzeuger am Bus.** Der Brenner ist hart abgeschaltet (Zustand vom
  Hausverkauf). Deshalb keine Kesseltelemetrie, keine Ist-Modulation. Die
  Betriebsstunden führt der Regler weiter (`ui BoilerHoursB1` ≈ 60836 h).
  *Folge: Heizfunktionen sind derzeit nicht thermisch verifizierbar — ein
  Schreibvorgang lässt sich nur zurücklesen. Warmwasser und Solar laufen.*
- **Ein Kollektorfeld.** `sc Coll2Sensor` meldet `cutoff`, ebenso
  `sc Storage3Sensor3`. Drei Speicherfühler sind aktiv.
- **Der MQTT-Zweig von ebusd ist abgeschaltet** (`seed_mqtt_cfg: false`,
  2026-09-01). Er stammte aus dem ersten Anlauf über MQTT und war nicht nur
  Altlast: der MQTT-Handler von ebusd setzt Poll-Prioritäten
  (`setPollPriority` → `addPollMessage`) und spannte damit den
  156-Nachrichten-Poll-Satz auf, aus dem sich der Zwischenspeicher füllte, den
  `find` liest. Diese Aufgabe hat jetzt `poll.py` mit 40 Registern. Die 158
  MQTT-Entitäten sind verschwunden.
- **`scan` springt immer wieder kurz auf `running`.** Vermutlich die beiden
  Master `0x3f` / `0x7f`, die sich nicht identifizieren lassen. Folge: ein
  Rescan wirft Nachrichten still aus der Poll-Liste. Deshalb prüft der
  Koordinator ihre Länge bei jedem Abruf.
- **Solarhysterese: 12 K ein, 5 K aus.** `SolEnableDiffTemp1` und
  `SolDisableDiffTemp1`, gemessen zwischen Kollektor 1 und **Speicher unten**
  (`Storage4Sensor3`), nicht gegen Speicher oben. Am 2026-09-01 live bestätigt:
  bei 70,7 °C Kollektor gegen 58,8 °C Speicher unten (11,9 K) lief die Pumpe an.
  Beide Register sind `r;w` vom Typ `temp0`, also nur ganze Kelvin. Weitere
  Solarparameter am Bus, bislang nicht eingebunden: `ScProtectionHysteresis`
  (30), `SolProtectionStartTemp` (130), `SolHwcMaxLoadTemp1` (90),
  `KolTempMin1` (0), `SolFlowRate` (3,50).
- **`sc SolCollPumpED1` ist kein Modulationsgrad.** Die Kollektorpumpe kennt
  nur 100 (ein) und 0 (aus). Sie ist deshalb ein Binärsensor, kein
  Prozentsensor -- eine Einschaltdauer, die nie zwischen den Werten steht,
  ist keine Kennzahl.
- **Keine brauchbare Raumtemperatur.** `ui RoomTemp` liefert zwar gültige Werte
  (~30 °C), das Bedienteil hängt aber im Heizungsraum. Als Führungsgröße
  bestätigt unbrauchbar.
- **`ui StateEM` und `ui DesiredDegreeB1..8` antworten nicht** (`no data
  stored`). Systemzustand kommt stattdessen aus `ui SystemModeStream1`.
- **Ertragsstatistik:** `ui YieldThisYear` / `YieldLastYear`, je zwölf
  Monatswerte in kWh. Nullwerte April–Juni 2026 sind echt (Heizung während des
  Verkaufs abgeschaltet), kein Dekodierfehler.

---

## 2. Was gebaut ist

`custom_components/auromatic/` — Phase 2 vollständig, Phase 3 teilweise.

| Modul | Inhalt |
|---|---|
| `ebusd.py` | Asynchroner TCP-Client für Port 8888, `parse_field`, Filterlogik |
| `coordinator.py` | `DataUpdateCoordinator`, ein `find` pro Kreis je Intervall |
| `poll.py` | Welche 41 Register ebusd aktiv vom Bus holen soll, in zwei Prioritäten |
| `entity.py` | `CircuitMixin` + Basisklasse, Gerätezuordnung per `via_device` |
| `config_flow.py` | Einrichtung inkl. Adress-Suche, Options-Flow für das Intervall |
| `sensor.py` | Temperaturen, Erträge, Laufzeiten, Systemzustand |
| `binary_sensor.py` | Störung, Kollektorpumpe, Telefonschalter, Frost- und Kollektorschutz |
| `select.py` | **Betriebsart** für `hc` und `mc` — der zentrale Steuerhebel |
| `number.py` | Raumsollwerte Tag/Absenkung, Heizkurve, Solarhysterese |
| `water_heater.py` | Speicher: Ist, Soll, Betriebsart |
| `diagnostics.py` | Vollständiger Registerbestand plus `ebusctl info`, Host redigiert |
| `strings.json`, `translations/` | Alle Oberflächentexte, englisch und deutsch |
| `icons.json` | Icons, nur wo keine `device_class` eins liefert |

Ein HA-Gerät je Bus-Adresse, alle per `via_device` am Regler.

### Verifiziert

- ebusd-Protokollschicht gegen wortgetreue Antwortdaten der Anlage
  (`tests/test_ebusd.py`, 32 Prüfungen).
- Vollständigkeit von Übersetzungen und Icons gegen den Entitätsbestand
  (`tests/test_translations.py`, 258 Prüfungen).
- Dataclass-Komposition der Description-Klassen (Mehrfachvererbung mit
  `frozen=True, kw_only=True`) gegen strukturgleiche Nachbauten.
- **Bedienung in Home Assistant:** Die Integration lädt, die Betriebsart lässt
  sich im Dropdown umstellen und bleibt stehen (`select.py` über
  `write_and_confirm`, siehe unten).
- **Vollständiger Entitätsbestand gegen die laufende Anlage** (2026-09-01, über
  die REST-API von HA 2026.8.3): 39 Entitäten, alle mit gültigem Wert, keine
  einzige `unavailable`. Solarschichtung monoton (Kollektor 75,4 → Speicher
  67,0 / 61,9 / 57,4 °C), Anlaufen der Kollektorpumpe live mitgelesen.
- **Jeder Schreibpfad am laufenden Regler**, je als Roundtrip (setzen, über
  den Bus zurücklesen, Ausgangswert wiederherstellen): Raumsollwerte Tag und
  Absenkung, Heizkurve und Betriebsart für **beide** Kreise, Warmwasser-
  Solltemperatur und -Betriebsart, Solar-Ein- und -Ausschaltdifferenz.
  12 von 12 sauber, anschließend alle Register nachweislich wieder auf dem
  Ausgangswert, kein Eintrag im Fehlerprotokoll. Dabei aufgefallen und behoben
  sind die beiden Fehler, die unter „Einzelfeld-Register" beschrieben sind.
- **0,5-K-Auflösung**: geschriebene 21,5 °C kommen als 21,5 °C zurück, für
  Heizkreis, Mischerkreis und Warmwasser.
- **Eigener Poll-Satz am Gerät** (2026-09-01, nach Abschalten des MQTT-Zweigs):
  `poll: 40`, `scan: finished`, keine Warnung im Protokoll, 39 Entitäten ohne
  eine einzige `unavailable`. Über acht Minuten gemessen liegt der Abstand
  zwischen zwei Messwerten bei **120–180 s** (Kollektor, Ertragsfühler,
  Außentemperatur) — im Raster des 60-Sekunden-Abrufs also rund 135 s, gegen
  **935 s** vorher. Siebenfach frischer bei unveränderter Buslast: Symbolrate
  32 von 166 möglichen, vorher 23 von 183.

### Nicht verifiziert

Vorausgesetzt wird **HA 2024.6+** (wegen `entry.runtime_data`); getestet auf
**HA 2026.8.3**. Nicht durchgespielt ist der Weg über die Oberfläche selbst --
geprüft wurde über Dienstaufrufe, die dieselben Entitätsmethoden ausführen.

---

## 3. Designentscheidungen und ihre Gründe

**ebusd behalten statt eigenem eBUS-Stack.** Das Decoding der Vaillant-Register
steckt in der Community-Konfiguration; nachbauen hieße Arbitrierung, CRC und
Registerdefinitionen neu schreiben. Gebaut wurde nur die HA-Schicht.

**Kommandoschnittstelle (8888) statt MQTT.** Der erste Anlauf des Nutzers über
MQTT scheiterte an fehlenden und falschen Werten. Über den Kommandoport werden
Schreibbefehle synchron quittiert, Fehler kommen als Rückgabewert.

**Drei Fallstricke, die jeden naiven Weg treffen** — alle in `ebusd.py`
behandelt und in den Tests abgedeckt:

1. Doppelte Nachrichtennamen (`FlowTempMax = 50` neben `FlowTempMax = no data
   stored`). Die leere Schreibvariante darf den Lesewert nicht überschreiben.
   *Wahrscheinlichste Ursache der fehlenden Werte im ersten Anlauf.*
2. Fühlerstatus `cutoff` bei nicht angeschlossenen Fühlern.
3. Kaskadenregister `B2`–`B8` liefern `ERR: invalid position`, weil die
   Definition acht Kessel kennt und einer verbaut ist.

**Nach dem Schreiben gezielt nachlesen.** Der Abrufzyklus lebt vom Cache
(`find`), aber ein Schreibbefehl aktualisiert dort nur die `Set*`-Nachricht --
die gleichnamige Lesenachricht bleibt bis zum nächsten Poll alt. Ein
`async_request_refresh()` holt also den alten Wert und stellt das
Bedienelement sofort wieder zurück. Deshalb `write_and_confirm()`: schreiben,
die Lesenachricht mit `read -f` einmal frisch vom Bus holen, das Ergebnis über
`async_set_updated_data()` in die Oberfläche geben. Schlägt das Nachlesen
fehl, gilt der geschriebene Wert -- ebusd hat den Schreibvorgang quittiert,
und der nächste Abruf korrigiert ohnehin.

**Eine Größe, ein Bedienelement.** Die Betriebsart stand doppelt in der
Oberfläche: als `select` und als Enum-Sensor. Der Select zeigt den aktuellen
Zustand bereits an und wird genauso in der Historie geführt, der Sensor war
reine Verdopplung. Er ist entfernt; der einzige Zustand, den der Select nicht
abbilden kann (`disabled` -- Kreis am Regler abgeschaltet), steht als eigene
Diagnose-Entität daneben (`key="mode_state"`, angezeigt als „Reglerzustand").

**Zwei Fehler, die erst die laufende Anlage gezeigt hat.** Die erste Fassung
der Poll-Anmeldung lief beim Setup einmal und danach nach Zeitplan. Am Gerät
fiel damit alles aus: ebusd lädt die CSV je Adresse erst beim Scannen, die
Anmeldung kam zu früh, und genau die Adressen oberhalb der gerade geladenen
scheiterten mit `ERR: element not found` — 0x15 (`ui`) und 0x25 (`hwc`) gingen
durch, 0x26 (`hc`), 0x50 (`mc`) und 0xec (`sc`) nicht. Ohne Poll-Satz blieb der
Zwischenspeicher leer, und weil Entitäten nur entstehen, wo beim Setup ein Wert
vorliegt, wurde die gesamte Integration `unavailable`.

Zweitens ist `mc RoomTempOffset` überhaupt nicht pollbar: `roomtempoffset.inc`
definiert es ausschließlich schreibend. ebusd hört den Wert nur passiv mit,
wenn das Bedienteil ihn setzt. Es steht deshalb in `POLL_PASSIVE`.

Beides führte zur heutigen Lösung: die Anmeldung wartet auf `scan: finished`,
fasst im Hintergrund nach — und vor allem prüft der Koordinator bei **jedem**
Abruf, ob die Poll-Liste noch die erwartete Länge hat. Ein Zeitplan genügt
nicht, weil ein Rescan die Einträge jederzeit entfernt und `scan` auf dieser
Anlage immer wieder kurz auf `running` springt. Die Länge der Liste ist das
einzige verlässliche Signal; sie kostet ein `info` je Abruf.

**Den Poll-Satz selbst anmelden statt die Konfiguration zu forken.** ebusd
pollt **eine** Nachricht pro `--pollinterval` — die Buslast hängt allein am
Takt, nie an der Länge der Liste. Mit den 156 Nachrichten des MQTT-Zweigs kam
ein Register alle 15,6 Minuten an die Reihe (gemessen: 935 s Median über sechs
Stunden, quer über alle Kreise gleich). Ein kürzerer Satz ist also ohne jeden
Mehrverkehr entsprechend schneller.

Naheliegend wäre eine eigene CSV-Definition gewesen. Nötig ist sie nicht: in
der gesamten Vaillant-Konfiguration steht keine einzige Poll-Priorität, die
Liste ist reiner Laufzeitzustand und wird per `read -p PRIO` gesetzt. Damit
entfällt eine geforkte Konfiguration, die bei jedem ebusd-Update nachzupflegen
wäre.

Die Liste ist eine Prioritätswarteschlange — nach jedem Abruf rückt eine
Nachricht um ihren Prioritätswert nach hinten, niedrige Zahl heißt häufiger.
Daraus die Zweiteilung in `poll.py`: 21 Messwerte auf Priorität 1, 20
Sollwerte und Zähler auf 9. Sollwerte brauchen die Warteschlange kaum, weil
`write_and_confirm` sie nach jeder Änderung ohnehin mit `read -f` frisch holt;
sie stehen nur drin, falls jemand direkt am Regler dreht. Rechnerisch kommt ein
Messwert damit alle ~2,3 Minuten dran, ein Sollwert alle ~21 — bei
unveränderter Buslast.

**Am Regelwerk von Home Assistant ausgerichtet** (Integration Quality Scale).
Umgesetzt: `has-entity-name`, `entity-unique-id`, `runtime-data`,
`test-before-setup`, `parallel-updates` (lesende Plattformen 0, schreibende 1),
`entity-unavailable`, `action-exceptions`, `entity-device-class`,
`entity-category`, `diagnostics`, `entity-translations`, `icon-translations`.
Offen bleiben allein die Punkte, die eine HA-Testumgebung voraussetzen
(`config-flow-test-coverage`, `test-coverage`) und die Platin-Stufe.

**Icons folgen der `device_class`.** Sie bestimmt Einheit, Darstellung und
Standardsymbol; ein eigenes `icon` steht nur da, wo es keine `device_class`
gibt (Enums, nackte Zahlen, Schalter) oder wo deren Symbol nichts über das
Gerät sagt -- `RUNNING` liefert nur ein Play-Zeichen, die Pumpe bekommt
`mdi:pump`. Temperaturen behalten bewusst ihr zustandsabhängiges Thermometer,
sonst geht die Ablesbarkeit gegen ein hübscheres Symbol verloren.

**Einzelfeld-Register statt Read-Modify-Write.** Ursprünglich war RMW auf
`Mode` geplant. Stattdessen wird auf die Register geschrieben, die die
ebusd-Konfiguration als `r;w` führt: `TempDesired` (`3200`), `TempDesiredLow`
(`3300`), `HeatingCurve` (`3500`), `OperatingMode` (`2B00`). Jedes trägt genau
ein Feld, Nachbarwerte werden nie berührt, und die Estrichtrocknung ist eine
Nachricht, die schlicht nie aufgerufen wird.

*Ein Umweg, der zwei Fehler gekostet hat:* zuerst liefen die Schreibvorgänge
über die `Set*`-Nachrichten aus `mcmode_inc.tsp`. Die gibt es aber nur im
Mischerkreis — `26.solsy.hc.tsp` bindet `hcmode_inc.tsp` ein, das keine
`Set*`-Nachrichten kennt. Im Heizkreis scheiterte deshalb **jeder** Schreibweg
mit `ERR: element not found`: Sollwerte, Heizkurve und Betriebsart. Und wo sie
existieren, sind sie gröber als das Register, das sie setzen: `SetTempDesired`
ist vom Typ `temp0` (ganze Grad), `TempDesired` vom Typ `temp1` (0,5 K). Am
Gerät gemessen: geschriebene 21,5 / 21,9 / 21,1 °C kamen alle als 21,0 zurück.
Die Oberfläche bot mit `native_step=0.5` also eine Auflösung an, die auf dem
Weg zum Regler verfiel.

**Betriebsart statt Regelkreis.** Erwogen war `mc RoomTempOffset` (±2 K,
Parallelverschiebung der Heizkurve) für einen ausfallsicheren Regelkreis. Der
Nutzer hat bewusst vereinfacht: Umschalten der Betriebsart genügt, bei
HA-Ausfall wird am Regler von Hand auf Zeitprogramm zurückgestellt.
`RoomTempOffset` bleibt als feinerer Hebel in Reserve und ist bereits als
Sensor eingebunden.

**Keine Zeroconf-Discovery.** ebusd nutzt mDNS nur, um den Adapter zu finden
(`--device=mdns:…` in `main.cpp`), meldet seinen eigenen Kommandoport aber
nicht an. Stattdessen klopft der Config-Flow an: Supervisor nach Add-ons mit
„ebusd" im Namen fragen, Hostnamen ableiten, zusammen mit Standardnamen
gleichzeitig probieren. Der Supervisor-Teil ist optional gekapselt, damit die
Integration auch auf HA Container und Core läuft.

---

## 4. Wenn etwas nicht funktioniert

**Erst prüfen, ob ebusd überhaupt Werte hat.** Die Integration erfindet nichts:

```
ebusctl info                    # Version, Signal, geladene CSVs je Adresse
ebusctl find -c mc              # Was der Kreis liefert
ebusctl read -c mc OperatingMode
```

Liefert `ebusctl` nichts, liegt es nicht an der Integration.

| Symptom | Erste Vermutung |
|---|---|
| Alles `unavailable` | ebusd nicht erreichbar. Hostname `2ad9b828-ebusd`, Port 8888. Diagnostics der Integration herunterladen. |
| Einzelne Entität fehlt | Register antwortet nicht oder meldet `cutoff`. Entitäten werden nur angelegt, wenn beim Setup ein gültiger Wert vorliegt — nach Änderungen an der Anlage Integration neu laden. |
| Werte springen oder sind absurd | Fühlerstatus nicht ausgewertet. `status_field` in der Beschreibung prüfen. |
| Schreiben schlägt fehl | Antwort von ebusd ansehen; `HomeAssistantError` trägt den Originaltext. Access-Level von ebusd prüfen. |
| Bedienelement springt zurück | Der Wert wurde geschrieben, aber aus dem Cache alt zurückgelesen. `ebusctl read -f -c mc OperatingMode` gegen `ebusctl find -c mc` halten. |
| Nichts geht mehr, ebusd meldet kein Signal | Zweiter Client auf Port 9999 des Adapters? Der Adapter erlaubt genau einen. |

**Nach Protokolländerungen:** neue Fixture in `tests/test_ebusd.py` ergänzen und
dort reproduzieren, bevor der Produktivcode angefasst wird. Die Tests laufen
ohne HA-Installation.

---

## 5. Offene Punkte

1. **`climate` bleibt der nächste inhaltliche Schritt** -- siehe Punkt 2.
   Alle Schreibpfade sind am Gerät geprüft, hier ist nichts mehr offen.
2. **`climate`-Entitäten.** Brauchen je Heizkreis einen HA-Sensor als
   Ist-Temperatur, weil der Regler keine brauchbare Raumtemperatur liefert.
   Kandidaten sind die Homematic-IP-Raumthermostate. Bis dahin bleiben
   Betriebsart und Sollwerte getrennte Entitäten.
3. **Bedarfsgeführte Fußbodenheizung.** Ventilstellungen kommen über die
   Homematic-IP-HCU-Integration. Geplant: Bedarf → `auto`, kein Bedarf →
   `low`, mit Totzone und Mindestabstand zwischen Schaltvorgängen (Trägheit
   liegt bei Stunden). Offen, ob die Stellantriebe Prozentwerte liefern
   (`HmIP-FALMOT-C12`) oder nur auf/zu.
4. **Kessel später ergänzen.** Wenn der Brenner wieder läuft, erscheint
   vermutlich eine `bai`-Adresse am Bus. Dann Scan wiederholen.
5. **Weitere Solarparameter**, falls gewünscht: Kollektorschutz-Schwelle und
   -Hysterese, maximale Speicherladetemperatur, Mindest-Kollektortemperatur.
   Alle `r;w`, alle vorhanden — bislang bewusst nicht eingebunden.
6. **HACS-Struktur** bewusst zurückgestellt.

## 6. Versionsverwaltung

Git-Repository auf Branch `main`, kein Remote. Der Anfangs-Commit enthält den
oben beschriebenen Stand vollständig.

Vor Änderungen an der Schreiblogik lohnt ein Blick in `CLAUDE.md`: die dortigen
Invarianten sind aus Fehlern und Anlagenwissen entstanden, nicht aus Vorsicht.
