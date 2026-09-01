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
| `entity.py` | `CircuitMixin` + Basisklasse, Gerätezuordnung per `via_device` |
| `config_flow.py` | Einrichtung inkl. Adress-Suche, Options-Flow für das Intervall |
| `sensor.py` | Temperaturen, Erträge, Laufzeiten, Systemzustand |
| `binary_sensor.py` | Störung, Telefonschalter, Frost- und Kollektorschutz |
| `select.py` | **Betriebsart** für `hc` und `mc` — der zentrale Steuerhebel |
| `number.py` | Raumsollwerte Tag/Absenkung, Heizkurve |
| `water_heater.py` | Speicher: Ist, Soll, Betriebsart |
| `diagnostics.py` | Vollständiger Registerbestand plus `ebusctl info` |

Ein HA-Gerät je Bus-Adresse, alle per `via_device` am Regler.

### Verifiziert

- ebusd-Protokollschicht gegen wortgetreue Antwortdaten der Anlage
  (`tests/test_ebusd.py`, 17 Prüfungen).
- Dataclass-Komposition der Description-Klassen (Mehrfachvererbung mit
  `frozen=True, kw_only=True`) gegen strukturgleiche Nachbauten.
- **Schreibpfad am echten Gerät:** `ebusctl write -c mc SetMode off` → `done`.

### Nicht verifiziert

**Die Home-Assistant-API-Oberfläche ist ungetestet** — HA war beim Bau nicht
installiert. Ob jede Plattform sauber lädt, zeigt sich erst beim ersten Start.
Erwartete Reibung: Importpfade oder Feldnamen, die sich zwischen HA-Versionen
verschoben haben. Vorausgesetzt wird **HA 2024.6+** (wegen `entry.runtime_data`).

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

**`Set*`-Einzelfeldnachrichten statt Read-Modify-Write.** Ursprünglich war RMW
auf `Mode` geplant; `mcmode_inc.tsp` definiert aber je Parameter eine eigene
Schreibnachricht. Damit werden Nachbarfelder nie berührt und die
Estrichtrocknung ist eine Nachricht, die schlicht nie aufgerufen wird.

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
| Nichts geht mehr, ebusd meldet kein Signal | Zweiter Client auf Port 9999 des Adapters? Der Adapter erlaubt genau einen. |

**Nach Protokolländerungen:** neue Fixture in `tests/test_ebusd.py` ergänzen und
dort reproduzieren, bevor der Produktivcode angefasst wird. Die Tests laufen
ohne HA-Installation.

---

## 5. Offene Punkte

1. **Erster Start in Home Assistant.** Der einzige wirklich ungeprüfte Teil.
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
5. **HACS-Struktur** bewusst zurückgestellt.

## 6. Versionsverwaltung

Git-Repository auf Branch `main`, kein Remote. Der Anfangs-Commit enthält den
oben beschriebenen Stand vollständig.

Vor Änderungen an der Schreiblogik lohnt ein Blick in `CLAUDE.md`: die dortigen
Invarianten sind aus Fehlern und Anlagenwissen entstanden, nicht aus Vorsicht.
