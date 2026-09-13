# Stand der Umsetzung

Stand: 2026-09-13. Planungsdokument mit Herleitung und Registerkarte:
<https://claude.ai/code/artifact/e2491a1b-e82f-496d-95e7-3a4633908ec0>

---

## 1. Die Anlage

| | |
|---|---|
| Regler | Vaillant auroMATIC 620/3, Kennung `SOLSY`, SW 0500 / HW 6301, Ident 0020076588, Serie `21161200200765880907005114N4`, KW 12/2016 |
| Bedienteil | `UI`, „CI of VRS 620/3", SW 0508 / HW 6201, Ident 0020080465, Serie `21161200200804650907005300N5` |
| Wärmeerzeuger | Vaillant `BAI00`, SW 0414 / HW 7401 — **wandhängende Therme (VC), kein Kessel**; keine Artikel-/Seriennummer am Bus, Elektronik `SB206740` |
| Adapter | eBUS Adapter Shield C6, ESP32-C6, PCB 2.44.0, **10.23.10.114** |
| Adapter-Modus | `ens` (enhanced) über TCP **Port 9999 — nur ein Client gleichzeitig** |
| ebusd | 26.1, als Home-Assistant-Add-on, Hostname **`2ad9b828-ebusd`**, Port 8888 |
| Home Assistant | HA OS / Supervised, **10.23.10.123** |

> **In diesem Netzsegment stehen mehrere Home-Assistant-Instanzen.** Die
> Heizungsinstanz ist **10.23.10.123**; der Name `homeassistant` löst auf eine
> andere auf (10.14.70.14), die zwar auf 8123 antwortet, aber kein ebusd hat.
> Am 2026-09-06 eine halbe Stunde gekostet: Port 8888 dort wies die Verbindung
> ab, und das sah aus wie eine fehlende Freigabe. Wer von außen an ebusd will,
> nimmt die **IP**, nicht den Namen.
>
> Von innerhalb von Home Assistant ist es weiterhin `2ad9b828-ebusd:8888` --
> so spricht auch die Integration mit ebusd, und dafür braucht es keinen
> veröffentlichten Port. Der ist nur für Werkzeuge von außen nötig, etwa
> `tools/thermal_log.py`.

### Bus-Teilnehmer

| Circuit | Adresse | Bedeutung | CSV |
|---|---|---|---|
| `bai` | 0x08 | **Wärmeerzeuger**, seit 2026-09-04 wieder am Bus | `vaillant/08.bai.csv` → `bai.0010006101.inc` |
| `ui` | 0x15 | Bedienteil, Systemebene | `vaillant/15.ui.csv` |
| `cc` | 0x23 | **Zirkulationskreis** | `vaillant/23.solsy.cc.csv` |
| `hwc` | 0x25 | Warmwasser | `vaillant/25.solsy.hwc.csv` |
| `hc` | 0x26 | Heizkreis (Radiatoren) | `vaillant/26.solsy.hc.csv` |
| `mc` | 0x50 | **Mischerkreis = Fußbodenheizung** | `vaillant/50.solsy.mc.csv` |
| `sc` | 0xec | Solarkreis | `vaillant/ec.solsy.sc.csv` |

Dazu der Brennermaster `0x03` (#11), der mit `0x08` zusammengehört und seit
dem 2026-09-04 mitzählt (`masters: 5`).

Zusätzlich führt ebusd zwei Master `0x3f` und `0x7f` (#23 und #24). **Sie senden keine Nutzlast.** In einer vollständigen Busaufnahme
(`grab result all`, 2026-09-01) stammt jedes einzelne Telegramm entweder vom
Bedienteil `0x10` oder von ebusd `0x31` — von `0x3f` und `0x7f` keines, und
ihre Slave-Adressen `0x44` / `0x84` erscheinen in `info` gar nicht erst. Die
Liste der gesehenen Master entsteht aus einzelnen Arbitrierungsbytes; genau so
sieht ein Bitfehler auf dem Bus aus. Unkritisch, und nicht mehr nur vermutet.

### Anlagenspezifische Besonderheiten

- **Der Wärmeerzeuger ist seit dem 2026-09-04 wieder am Bus.** Bis dahin war
  er hart abgeschaltet (Zustand vom Hausverkauf), und deshalb stand hier
  jahrelang „kein Kessel, keine Telemetrie, Heizfunktionen nur zurücklesbar".
  Der Benutzer hat ihn an jenem Vormittag eingeschaltet und den Heizkreis
  getestet. Am Bus meldete sich `MF=Vaillant;ID=BAI00;SW=0414;HW=7401` auf
  `0x08`, dazu der Master `0x03`.

  ebusd hatte für die Adresse zunächst **keine Definition geladen** — `info`
  zeigte sie als `scanned`, aber ohne `loaded`, und `find -c bai` antwortete
  mit `ERR: element not found`. Ein `ebusctl reload` genügte; die
  Konfiguration wird beim Start dynamisch geladen, und beim damaligen Start
  war der Brenner eben noch stromlos. Geladen wird `vaillant/08.bai.csv` und
  darüber `bai.0010006101.inc` — nicht über die Hardware-Kennung, sondern über
  den Zweig `[Scan_id_product='']`: die Artikelnummer kommt bei diesem Gerät
  leer zurück (`Scan.08 Id = ;;;;;;`), und genau der Leerwert steht in der
  Bedingungsliste. Danach: `messages: 774` statt 550, 224 neue Nachrichten.

  Der Poll-Satz fiel dabei kurz auf 0 und stand beim übernächsten Abruf wieder
  bei 41 — Invariante 6 hat gehalten, ohne Zutun.
- **Der Brenner meldete am 2026-09-04 F.75 und die Heizungspumpe saß fest.**
  F.75 heißt bei Vaillant: beim Anlaufen der Pumpe wurde kein Druckanstieg
  erkannt. Zwei Ursachen kommen dafür in Frage, zu wenig Wasser oder eine
  Pumpe, die sich nicht dreht — `bai WaterPressure` stand bei **1,461 bar**,
  also blieb die zweite. Der Benutzer hat die Pumpe von Hand gangbar gemacht.

  Beides ist am Gerät belegt und nicht erzählt:

  | Register | Wert | |
  |---|---|---|
  | `bai Errorhistory` | `1;-:-;-.-.-;75` | 0x4b = 75 = F.75 |
  | `bai Currenterror` | `-;-;-;-;-` | nichts mehr anstehend |
  | `bai WaterPressure` | 1,461 bar, `ok` | kein Wassermangel |
  | `bai HcPumpMode` (d.18) | `post_run` | die Pumpe läuft nur auf Anforderung |
  | `bai WaterpressureBranchControlOff` | `off` | die Drucksprungerkennung ist **nicht** abgeschaltet |
  | `bai DeactivationsIFC` (d.61) | 1 | ein einziger Zündfehler im ganzen Gerätleben |

  Die Ursache steht in `HcPumpMode`: die Pumpe läuft im Nachlaufbetrieb, also
  nur auf Anforderung. Den Blockierschutz fährt der Brenner selbst, aber nur
  solange er Strom hat — und den hatte er seit dem Hausverkauf nicht. Jetzt,
  wo er wieder unter Spannung steht, macht er das täglich von allein.

  Der Testlauf selbst steckte noch im Mitschnitt von ebusd. `grab result all`
  zeigt die Anforderungstelegramme des Reglers an den Brenner
  (`1008b51009 …`, die Nachricht `bai SetMode`) getrennt nach Inhalt: 3193-mal
  mit `flowtempdesired` 0,0 und `disablehc` 1, dazu 255 Telegramme mit
  aufgehobener Sperre und einem Sollwert, der von 25,0 über 34,0 auf 39,0 °C
  lief. Bei einem Takt von rund 17 s (aus 974 Datetime-Broadcasts à 1/min
  gerechnet) sind das **rund 70 Minuten Wärmeanforderung**.
- **Wartung am 2026-09-10, am Bus nachweisbar.** Wartung und Schornsteinfeger
  waren an dem Tag da; nachgelesen wurde am 2026-09-13. Vier Register tragen
  etwas dazu bei, zwei davon eindeutig:

  | Register | Wert | |
  |---|---|---|
  | `ui ServicePeriod` | **10.09.2027** | genau ein Jahr nach der Wartung — der Termin ist gesetzt worden |
  | `bai WaterPressure` | 1,461 → **1,550 bar** | beide Male kalt gemessen, also nachgefüllt |
  | `bai Errorhistory` | Platz 0 und 1 auf **70**, Platz 2–9 auf 75 | zwei neue Einträge seit dem 2026-09-04 |
  | `bai HoursTillService` | 3010 h | vorher nie protokolliert, kein Vergleich möglich |

  `ui ServicePeriod` ist der belastbarste der drei: ein Datum, das niemand
  anders setzt als der, der die Wartung gemacht hat. Es ist seit dem
  2026-09-13 als Entität eingebunden (Sensor „Wartung" am Regler,
  `SensorDeviceClass.DATE`) — vorher stand nirgends, wann zuletzt jemand an
  der Anlage war.

  Der Wasserdruck ist der Vergleich zweier kalter Messungen: 1,461 bar am
  2026-09-04 bei stehendem Brenner, 1,550 bar am 2026-09-13 bei 24 °C
  Vorlauf. Die 1,541 bar vom 2026-09-06 gehören nicht in die Reihe, die waren
  an der warmen Anlage gemessen und reine Ausdehnung.

  Die beiden Fehlereinträge stehen bei den offenen Punkten (11) — sie sind
  der erste Fall, in dem der fehlende Fehlerspeicher tatsächlich etwas
  verdeckt hat.

  *Vom Schornsteinfeger selbst ist nichts abzulesen.* `hc CleaningLady` und
  `mc CleaningLady` — die Schornsteinfegerfunktion des Reglers — stehen auf 0,
  und eine Abgasmessung dauert Minuten, verschwindet also in der
  Stundenauflösung aller Zähler. Ihre Spur wäre höchstens ein Brennerstart
  unter vielen.

- **Eine Woche Heizbetrieb, ohne eine volle Betriebsstunde** (2026-09-06 →
  2026-09-13). Die Zähler:

  | Register | 06.09. | 13.09. | |
  |---|---|---|---|
  | `bai HcHours` | 7098 | 7098 | unverändert |
  | `bai HwcHours` | 657 | 657 | unverändert |
  | `bai FanHours` | 8174 | 8174 | unverändert |
  | `bai FanStarts` | 50 255 | 50 262 | +7 |
  | `bai HcPumpStarts` | 54 820 | 54 845 | +25, also ≈3,6/Tag |
  | `ui BoilerHoursB1` | 60 836 | 60 838 | +2 |
  | `bai DeactivationsIFC` | 1 | 1 | unverändert |

  Das passt zusammen und ist kein Ausfall: die Heizkreise standen am
  2026-09-13 auf `low`, `bai SetMode` trug weiterhin `disablehc` = 1, draußen
  waren 17,4 °C. Der Regler fordert schlicht keine Wärme an. Sieben
  Gebläsestarts in sieben Tagen sind Warmwasser und Blockierschutz, nicht
  Heizbetrieb.

  **Für Punkt 4 heißt das: die Woche zählt nicht.** Der Nachweis unter Last
  braucht aufgedrehte Thermostate und Wärmeabnahme, nicht Laufzeit.

- **Der Heizversuch vom 2026-09-06 (09:47–10:47, 120 Messzeilen à 30 s).**
  Erster Betrieb der Heizfunktionen unter Beobachtung, aufgezeichnet mit
  `tools/thermal_log.py`. Geschaltet wurde ausschließlich über Home Assistant.

  *Die Schreibkette, Station für Station:* 10:11:07 Betriebsart noch `off`,
  Sollwert 0, Heizsperre 1. **10:11:37** Betriebsart `on`, `hc FlowTempDesired`
  springt auf **41,0 °C**. **10:12:07** `bai SetMode` trägt **42,0** und die
  Heizsperre fällt auf 0. 10:12:37 Pumpe läuft, 10:13:37 Flamme. Kesselvorlauf
  24 → 45 °C. Damit ist der Schreibpfad an einer Temperatur gemessen und nicht
  mehr nur zurückgelesen.

  *`sc SumBackflowSensor` ist der Sammelrücklauf.* Grundlinie 26,94–27,56 °C
  über 24 Minuten — mit einem Wechsel der Solarpumpe mittendrin, den er nicht
  bemerkt hat. Im Heizbetrieb Spitze **42,94 °C**, ein Hub von **15,4 K**,
  beginnend 90 s nach dem ersten Heizwasser. Damit sind beide Hälften der
  Zuordnung, die negative wie die positive, in einem Datensatz belegt.

  *Die Kesselüberhöhung ist nicht konstant.* Beobachtete Paare Regler/Kessel:
  40,0/42,0 · 40,0/43,0 · 40,5/42,0 · 41,0/42,0 · 41,0/43,0 — also 1,5–3 K,
  und in verschiedenen Schrittweiten (Regler 0,5 K, Kessel 1 K).

  *Die Heizkurve rechnet live.* Außentemperatur 14,12 → 15,12 °C, Sollwert
  dabei 41,0 → 40,5 → 40,0.

  *Der Mischer bleibt zu.* `mc FlowTemp` 23,50–26,81 °C, während der
  Systemvorlauf auf 41 °C stand: 2,75 K passiv. Invariante 3 hält.

  *Es gab keine Wärmeabnahme.* Die Pumpe lief 90 s und stand dann 31 Minuten,
  obwohl die Anforderung durchgehend anlag; Spreizung konstant +3,0 K;
  Abkühlrate danach 0,07 K/min; **ein einziger Brennerzyklus in 33 Minuten**.
  Die kurze Pumpenlaufzeit ist dabei die Folge, nicht die Ursache: der Brenner
  war nach 90 s am Sollwert, weil niemand Wärme abnahm. Die Heizkörper waren
  vermutlich geschlossen — der Kellerkreis ist ohnehin hart abgesperrt. Der
  Nachweis unter Last steht damit noch aus, siehe Punkt 4.

  *Der Wasserdruck taugt nicht als Nachweis des Pumpenanlaufs.* 1,452–1,461 bar
  über den ganzen Lauf, in Stufen von ~9 mbar. Der Brenner wertet den
  Drucksprung in den Sekunden nach dem Anlaufen aus; bei 30 s Abtastung und
  dieser Auflösung ist er nicht messbar. Der Beleg ist die **ausbleibende
  F.75**.

  Auf Temperatur reagiert derselbe Wert dagegen sehr wohl: zwei Stunden nach
  dem Versuch, mit noch warmer Anlage, stand er bei **1,541 bar** — 80 mbar
  über dem Ausgangswert, reine Wärmeausdehnung. Der Fühler ist also nicht zu
  träge, der Effekt beim Pumpenanlauf ist zu schnell und zu klein.

  *Zählerstände:* `HcPumpStarts` 54 819 → 54 820, alles andere unverändert.
- **Der Warteschlangentakt macht Zustandsanzeigen unbrauchbar.** Im selben Lauf
  gemessen: `bai Flame` meldete den Brennerstart **60–90 s zu spät**,
  `bai Statenumber` stand bei brennender Flamme noch auf 31 („kein
  Wärmebedarf"), `HcPumpStarts` zeigte den Pumpenstart erst nach Minuten.
  Zeitgerecht war ausschließlich, was das Bedienteil selbst pollt:
  `bai Status01` und `bai SetMode` mit 17 s. Für ruhende Werte ist die
  Warteschlange richtig, für Zustände nicht — siehe Punkt 10.
- **Ein Ausreißer mit gültigem Fühlerstatus.** Am 2026-09-06 um 10:15:37
  meldete `hc SumFlowSensor` **16,81 °C** zwischen 33,62 und 39,12 — 11 K
  daneben, eine einzelne Abtastung, danach wieder exakt auf der Kurve, und das
  Statusfeld stand auf `ok`. Gegenprobe im selben Moment: `ui FlowTemp` und
  `hc SumFlowSensor` frisch vom Bus lieferten 39,12 und 39,31. Das ist die
  unangenehme Variante des Problems aus Invariante 4: dort meldet ein fehlender
  Fühler wenigstens `cutoff`, hier kommt ein plausibler Zahlenwert mit gültigem
  Status und landet als Spitze in der Langzeitstatistik. **Ein einzelnes
  Vorkommnis rechtfertigt keinen Plausibilitätsfilter** — ein solcher Filter
  versteckt am Ende echte Sprünge. Der Eintrag steht hier, damit ein zweites
  Vorkommnis nicht wieder als Einzelfall durchgeht.
- **Der Wärmeerzeuger ist eine Therme, kein Kessel.** Der Unterschied ist die
  Bauform: „Kessel" ist bodenstehend mit großem Wasserinhalt, „Therme"
  wandhängend und kompakt — normativ heißt beides Heizkessel, „Therme" ist
  Sprachgebrauch. Bei Vaillant ist `VC` das wandhängende Heizgerät, `VCW`
  dasselbe mit Warmwasserbereitung, `VK` der bodenstehende Kessel. Dieses
  Gerät ist ein VC: `bai HwcTemp` meldet `circuit` (kein WW-Vorlauffühler),
  `bai StorageTemp` meldet `cutoff` (kein geräteseitiger Speicher), und der
  Benutzer hat es am 2026-09-05 als wandhängend ohne Speicher bestätigt; die
  Kennung `BAI00` gehört zur atmoTEC/turboTEC-Reihe. Das Gerät heißt in Home
  Assistant deshalb seit 0.3.2 **„Therme"**.

  *Welche der beiden Reihen, sagt das Gebläse.* Am 2026-09-06 am Bus gelesen:
  `bai FanHours` = 8174, `bai FanStarts` = 50 255 (der Startzähler nur
  nachrichtlich — er ist `UIN` und steht dicht unter der 16-Bit-Decke, siehe
  offener Punkt 9; das Argument hier trägt allein der Stundenzähler). Dem stehen 7755
  Brennerstunden gegenüber (7098 Heizen + 657 Warmwasser) — das Verhältnis ist
  das eines Gebläses, das bei jedem Zyklus mit Vor- und Nachspülung mitläuft.
  Ein **atmoTEC** ist im Naturzug ausgelegt und hat kein Gebläse, sondern eine
  Strömungssicherung; das Gerät gehört also zur **turboTEC**-Seite. Dazu passen
  `bai AircontrolOk`, die APS-Zähler (Luftdruckwächter) und `bai Fluegasvalve`.

  Der Benutzer hat am Gehäuse einen Ansaugstutzen gesehen und daraus auf ein
  raumluftabhängiges Gerät geschlossen — der Stutzen ist aber gerade das
  Merkmal des gebläseunterstützten. Beides schließt sich nicht aus: ein
  turboTEC darf einrohrig betrieben werden (B23), Abgas über den Schornstein
  und Verbrennungsluft aus dem Aufstellraum. Dann verhält es sich im Betrieb
  raumluftabhängig, obwohl es ein turboTEC ist. Verbindlich entscheidet das
  Typenschild; für die Integration ändert sich dadurch nichts.

  *Die Heizleistung ist begrenzt.* `bai PartloadHcKW` = 12 gegen
  `bai PartloadHwcKW` = 23: die Teillast für den Heizbetrieb steht auf 12 kW.
  Das schärft den Befund des Heizversuchs — selbst mit halber Leistung war der
  Sollwert nach 90 Sekunden erreicht. Ohne Abnahme hilft auch eine kleine
  Flamme nichts.

  Praktische Folge für die Messung: eine Therme hat wenige Liter Wasserinhalt.
  Eine schnelle Aufheizrate ist deshalb eine Eigenschaft des Geräts und kein
  Befund über die Last — und an einer Sommerlast taktet sie zwangsläufig, weil
  ihr die Puffermasse fehlt.
- **Die Typenbezeichnung steht nicht auf dem Bus, die Seriennummer schon.**
  `scan result` liefert je Teilnehmer zwölf Spalten: Adresse, Hersteller,
  Kennung, SW, HW und die sieben Felder der Nachricht `Scan.<zz> Id`
  (`prefix`, `year`, `week`, `product`, `supplier`, `counter`, `suffix`). Die
  sieben sind 2+2+2+10+4+6+2 = **28 Zeichen breit und ergeben aneinandergehängt
  genau die Seriennummer vom Typenschild**. `supplier=0907` ist dabei kein
  Datum, sondern der Lieferantencode; das Baujahr steckt in `year`/`week`.

  Am Brenner sind diese sieben Felder **leer** — kein Zufall, sondern der
  Grund, aus dem ebusd seine Definition über `[Scan_id_product='']` lädt. Was
  er hat: `bai SerialNumber` (HEX:8, „Seriennummer AI"), als ASCII gelesen
  `SB206740`, und `bai BoilerType = 6` — ein Code ohne Werteliste in der
  ebusd-Definition, die Zuordnung zur Gerätereihe steht nur in Vaillants
  Servicedokumentation. Ebenso `bai DSN` = 5148.

  Eine Artikelnummer trägt das Gerät doch, nur nicht für sich selbst:
  `bai PartnumberBox` = `00 20 09 24 78`, also **0020092478** — dasselbe
  Zahlenformat wie bei Regler (0020076588) und Bedienteil (0020080465), aber
  die Nummer der Elektronikbox, nicht der Therme.
- **`ui BoilerHoursB1` sind Ansteuerstunden, jetzt zweifach belegt.** Der
  Regler meldet 60 836 h, der Brenner selbst zählt 7098 h Heizbetrieb
  (`bai HcHours`), 657 h Warmwasser und 8174 h Lüfter. Die Zahlen haben
  nichts miteinander zu tun. Bisher stand die Auslegung als „Ansteuerstunden"
  auf dem Kommentar der archivierten CSV; jetzt steht sie auf den Zählern des
  Geräts.
- **Zwei Wege zur Zirkulationspumpe.** `bai AccessoriesOne` (d.27) steht auf
  `circulationpump`, `bai AccessoriesTwo` (d.28) auf `extheatingpump` — das
  Zubehörrelais 1 des Kessels ist also als ZP konfiguriert, und `bai CirPump`
  (d.13) zeigt dessen Zustand. Neben dem ZP-Ausgang des Reglers gibt es damit
  einen zweiten möglichen Anschlusspunkt. Gehört zu Punkt 8 der offenen
  Punkte: vor dem Rückbau ist zu klären, welcher verdrahtet ist.
- **Was der Kessel nicht hat.** `bai HwcTemp` meldet `circuit` (kein
  WW-Vorlauffühler — ein VC, kein Kombigerät), `bai StorageTemp` `cutoff`
  (kein kesselseitiger Speicherfühler, der Solarspeicher hängt am Regler),
  ebenso `bai OutdoorstempSensor` und die beiden Abgasfühler `AITemp` /
  `AATemp`. Der Fühlerstatus `circuit` war neu — bis dahin kannten die
  Fixtures nur `cutoff`. Invariante 4 sortiert beide gleich aus.
- **Die Prädiktivwartung des Kessels ist wertlos.** `bai OverflowCounter`
  steht auf `yes`, die Zähler sind also übergelaufen;
  `bai WaterpressureVariantSum` liefert 65 529 mbar, und drei `Pred*`-Register
  scheitern schon am Dekodieren (`ERR: invalid position in decode`). Rund 30
  Register, die keine Entität bekommen.
- **Am Kessel wird nichts geschrieben.** Seine beschreibbaren Register liegen
  ausnahmslos auf Installateur- und Serviceebene (`wi`/`ws`), und in derselben
  Reihe steht `SetFactoryValues` (d.96 Werkseinstellungen). Dieselbe Logik wie
  Invariante 1: eine Nachricht, die nie aufgerufen wird, kann auch nichts
  verstellen.
- **Ein Kollektorfeld.** `sc Coll2Sensor` meldet `cutoff`, ebenso
  `sc Storage3Sensor3`.
- **Die vier `Storage*Sensor3` sind nicht vier Speicherhöhen.** Der Regler
  listet in Menü 6 („Solarspeicher — Information") „Speicherfühler 1",
  „Speicherfühler 2", „Speicherfühler 3", „Fühler TD1", „Fühler TD2"
  (Bedienungsanleitung 0020094390, Kap. 5.10). `Storage1..3Sensor3` sind die
  Speicherfühler — 1 oben, 2 unten, 3 hier nicht angeschlossen —,
  `Storage4Sensor3` ist **TD1** aus der Differenztemperaturregelung und damit
  gar kein Speicherfühler. Die Messwerte des 2026-09-01 bestätigen das:
  `Storage2Sensor3` fährt die glatte Solarladekurve (59,6 °C um 13:20 auf
  65,1 °C um 16:45, danach langsam fallend), `Storage4Sensor3` springt
  denselben Tag ohne Trend zwischen 55,9 und 61,4 °C. Nur zwei aktive
  Speicherfühler also, nicht drei.
- **Der MQTT-Zweig von ebusd ist abgeschaltet** (`seed_mqtt_cfg: false`,
  2026-09-01). Er stammte aus dem ersten Anlauf über MQTT und war nicht nur
  Altlast: der MQTT-Handler von ebusd setzt Poll-Prioritäten
  (`setPollPriority` → `addPollMessage`) und spannte damit den
  156-Nachrichten-Poll-Satz auf, aus dem sich der Zwischenspeicher füllte, den
  `find` liest. Diese Aufgabe hat jetzt `poll.py` mit 51 Registern. Die 158
  MQTT-Entitäten sind verschwunden.
- **`scan` springt immer wieder kurz auf `running`.** Vermutlich die beiden
  Master `0x3f` / `0x7f`, die sich nicht identifizieren lassen. Folge: ein
  Rescan wirft Nachrichten still aus der Poll-Liste. Deshalb prüft der
  Koordinator ihre Länge bei jedem Abruf.
- **Solarhysterese: 12 K ein, 5 K aus — gemessen gegen Kollektor 1 und
  Speicherfühler 2 (unten).** `SolEnableDiffTemp1` und `SolDisableDiffTemp1`.
  Der Bezugsfühler war offen und ist es seit dem 2026-09-02 nicht mehr: über
  16 Schaltvorgänge eines Tages, mit Messwerten von 14–45 s Alter, liegen die
  Differenzen so:

  | Bezug | EIN (soll 12 K) | AUS (soll 5 K) |
  |---|---|---|
  | **Speicherfühler 2 unten** | 13,44 ±1,70 | **4,19 ±1,97** |
  | TD1 (`Storage4Sensor3`) | 18,69 ±4,41 | 10,54 ±3,29 |
  | Speicherfühler 1 oben | 5,84 ±2,37 | −2,50 ±2,14 |

  Der Ausschaltpunkt streut bei Speicher unten von beiden Seiten um genau
  5 K, die Einschaltwerte liegen dicht über 12 K — bei einem Abtastabstand von
  120 s und steigendem Kollektor genau das erwartete Bild. TD1 passt weder in
  der Lage noch in der Streuung. Die einzelne Beobachtung vom 2026-09-01
  (11,9 K gegen TD1) war Zufall; sie stand auf Werten, die bis zu 135 s alt
  sein konnten. Damit gilt, was die Bedienungsanleitung beschreibt: „Differenz
  zwischen Kollektortemperatur und Speichertemperatur".

  Beide Register sind `r;w` vom Typ `temp0`, also nur ganze Kelvin. Die
  übrigen Solarparameter sind seit 2026-09-02 als Diagnose-Sensoren
  eingebunden: `SolProtectionStartTemp` (130), `ScProtectionHysteresis` (30),
  `SolHwcMaxLoadTemp1` (90), `KolTempMin1` (0), `SolFlowRate` (3,50).
  Lesend, obwohl alle fünf `r;w` und einfeldrig sind — es sind die
  geräteseitigen Absicherungen des Kollektorkreises, dieselbe Sorte Wert wie
  `mc FlowTempMax`, den Invariante 3 aus demselben Grund schreibgeschützt
  lässt. `SolFlowRate` ist ohnehin kein Stellwert, sondern die Auslegungsgröße,
  mit der der Regler den Ertrag rechnet.
- **Die Reglerbezeichnungen stehen in der archivierten CSV, nicht in der
  TypeSpec-Fassung.** `john30/ebusd-configuration` führt die aktuelle
  Definition unter `src/vaillant/*.tsp` — dort ist jedes Register nur mit einem
  englischen Entwicklerkommentar beschriftet. Die aufgelöste Altfassung unter
  `archived/de/vaillant/*.csv` trägt zusätzlich die **deutsche Bezeichnung aus
  dem Reglermenü** und einen ausführlicheren Kommentar. Wo die Frage lautet
  „wie heißt das Ding am Gerät", ist das die Quelle:

  | Register | Bezeichnung in der CSV | Kommentar |
  |---|---|---|
  | `sc SolProtection` | Solarkreisschutzfunktion, `r;w` | „Wird der hier eingestellte Wert überschritten, dann wird die Kollektorpumpe des betroffenen Kreises zum Schutz vor Überhitzung der Komponenten abgeschaltet. Die Kollektortemperatur muß 30K unter diesen Wert sinken um die Schutzfunktion zu verlassen." |
  | `sc Storage4Sensor3` | **TD1 Sensor** | englisch daneben „Temperature of SP4 sensor" — der englische Kommentar ist hier der falsche |
  | `sc SumBackflowSensor` | **TD2 Sensor** | — |
  | `sc SolEnableDiffTemp1` | Einschaltdifferenz 1 | „Temperaturdifferenz zwischen KOL1 und **Speicher unten** ab der die Kollektorpumpe gestartet wird" |

  Damit sind drei Dinge belegt, die vorher auf Messreihen standen: der
  Bezugsfühler der Solarhysterese ist der Speicher unten (nicht TD1),
  `SolProtection` ist die *Freigabe* der Schutzfunktion und kein Zustand, und
  die 30 K aus ihrem Kommentar sind genau `ScProtectionHysteresis`. Die
  Beschriftung „Fühler TD1" für `Storage4Sensor3` ist bestätigt.

  Nicht belegt ist damit der Name „Sammelrücklauf" für `SumBackflowSensor`:
  die CSV nennt ihn TD2. Beides stimmt — das Register *ist* der TD2-Eingang
  der Differenztemperaturregelung, der Fühler daran hängt an dieser Anlage im
  Heizungsrücklauf (14 h Messreihe, kein Ausschlag bei sechs Pumpenzyklen).
  Der Oberflächenname beschreibt, was er misst, nicht, wo er angeklemmt ist.
- **`sc SolCollPumpED1` ist kein Modulationsgrad.** Die Kollektorpumpe kennt
  nur 100 (ein) und 0 (aus). Sie ist deshalb ein Binärsensor, kein
  Prozentsensor -- eine Einschaltdauer, die nie zwischen den Werten steht,
  ist keine Kennzahl.
- **`sc YieldSensor` sitzt im Solarrücklauf, `sc SumBackflowSensor` gar nicht
  im Solarkreis.** Am 2026-09-01 über 14 Stunden mit sechs Pumpenzyklen
  (Kollektor 63,4–77,1 °C) nachgemessen. Der Ertragsfühler folgt dabei dem
  Speicher unten (Mittel +1,3 K, Streuung ±1,9 K) und nicht dem Kollektor
  (−10,6 K, ±4,6 K); zweimal liegt er sogar unter dem Speicher unten, was ein
  Vorlauffühler bei laufender Pumpe nicht kann. Er ist die kalte Seite der
  Ertragsrechnung — heiße Seite ist der Kollektorfühler, Durchsatz
  `SolFlowRate` 3,50 l/min, um 15:37 also 10,4 K ≈ 2,3 kW. **Einen
  Vorlauffühler hat der Solarkreis nicht.** `SumBackflowSensor` dagegen stand
  denselben Tag monoton bei 26,06–26,50 °C, ohne einen Ausschlag bei irgendeinem
  Pumpenzyklus, und schwingt abends auf dasselbe Kellerniveau aus wie der
  Ertragsfühler (26,4 gegen 27,1 °C), während der Kollektor auf 24,7 °C fällt.
  Er ist der Sammelrücklauf der Heizung, Gegenstück zu `hc SumFlowSensor`
  (Sammelvorlauf, 23,75 °C = `ui FlowTemp`), und tot auf Raumtemperatur, weil
  der Brenner abgeschaltet ist — angeschlossen ist er (Status `ok`, ein
  abgeklemmter Fühler meldet `cutoff`). Die Oberflächennamen sind deshalb
  seit 2026-09-01 „Solarrücklauf" (`yield_sensor`) und „Sammelrücklauf"
  (`backflow`). Der Circuit `sc` bleibt für `backflow` stehen, obwohl `hc` die
  ehrlichere Zuordnung wäre: ein Wechsel ändert die `unique_id` und wirft die
  Historie der Entität weg.
- **`RoomTempOffset` gibt es auch am Heizkreis.** Das Bedienteil schreibt den
  Offset (`b505 04 2d 00`) in der Busaufnahme 61-mal auf `0x50` *und* 61-mal
  auf `0x26`, beide Kreise quittieren. Auf Busebene existiert das Register also
  in beiden; es fehlt nur in der ebusd-Definition, weil `hcmode_inc.tsp` das
  `roomtempoffset.inc` nicht einbindet. Der feinere Regelhebel ist damit nicht
  auf den Mischerkreis beschränkt.
- **Niemand sonst schreibt auf unsere Register.** Alle Schreibtelegramme der
  Aufnahme kommen vom Bedienteil und gehen auf `2d00` (RoomTempOffset), `2700`
  und `2b0f` (beide unbekannt, in keiner CSV). Unsere Ziele `2b00`, `3200`,
  `3300` und `3500` beschreibt kein anderer Teilnehmer.
- **`hc SumFlowSensor` pollt ebusd nicht selbst.** Es steht in der Poll-Liste
  (`poll: 38`) und hat einen gültigen Wert (22,81 °C, `ok`), aber in der
  Aufnahme sendet ebusd dafür keine einzige eigene Anfrage — das Bedienteil
  fragt es 256-mal ab, ebusd schneidet mit. Offenbar überspringt der Poll, was
  ohnehin frisch im Zwischenspeicher liegt. Fremdverkehr auf dem Bus
  verbilligt unseren Satz also, statt ihn zu stören. Am Tagesverlauf des
  2026-09-02 abzulesen: der Sammelvorlauf wechselte im Median alle **30 s**
  seinen Wert, alles selbst Gepollte alle 120 s.
- **Zwei Messwerte standen doppelt am Bus.** Am 2026-09-02 zeitgleich
  verglichen: `ui FlowTemp` und `hc SumFlowSensor` liefern dieselbe
  Wertfolge (99 Paare, höchstens 0,63 K auseinander — der Zeitversatz der
  beiden Abrufe), `hwc Storage1Sensor2` und `sc Storage1Sensor3` denselben
  Fühler (140 Paare, höchstens 0,24 K, zu 90 % unter 0,1 K). Vier Plätze in
  der Warteschlange für zwei Temperaturen. Geblieben sind `hc SumFlowSensor`
  (das Bedienteil hält es umsonst frisch) und `hwc Storage1Sensor2` (der
  Warmwasserkreis braucht es ohnehin); die Solar-Entität „Speicherfühler 1
  oben" liest seit dem 2026-09-02 über `source_circuit` aus dem
  Warmwasserkreis und behält dabei Gerät, Namen und Historie. Die Entität
  „Systemvorlauf" ist ersatzlos entfallen — sie zeigte, was der Sammelvorlauf
  schon zeigt.
- **`sc SolProtection` ist eine Einstellung, kein Zustand.** Es stand vom
  2026-09-01 bis 2026-09-02 durchgehend auf `on`, über 27 Stunden hinweg, bei
  Kollektortemperaturen von 14 °C nachts bis 89 °C mittags — gegen eine
  Schutzschwelle von 130 °C (`SolProtectionStartTemp`). Gemeldet wird die
  freigegebene Funktion, nicht ihre Auslösung. Es liegt deshalb seit dem
  2026-09-02 auf Stufe 9 und ist ohnehin eine Diagnose-Entität. *Offen: die
  Nachrichtendefinition selbst (`ebusctl find -e -c sc SolProtection`) würde
  den Namen bestätigen; die Messreihe genügt für die Poll-Stufe, nicht
  zwingend für die Beschriftung.*
- **`cc` ist der Zirkulationskreis, nicht der „Zentralteil".** Der Kopf von
  `23.solsy.cc.csv` nennt ihn `0020080463 163 Circulation`, und die Anlage
  bestätigt es (2026-09-03, direkt über `ebusctl` gelesen):
  `cc Mode = 30;auto;off;circulation;00;night` — das Feld `mctype` steht auf
  `circulation`, der ZP-Ausgang des Reglers ist also konfiguriert. Der Kreis
  lädt dieselben Includes wie das Warmwasser (`hwcmode.inc`, `timer.inc`) und
  bringt damit alles mit, was zur Steuerung nötig ist:

  | Register | Typ | Bedeutung | Wert am 2026-09-03 |
  |---|---|---|---|
  | `cc Mode` Feld 2 | `r`, `hwcmode` | Betriebsart der Zirkulationspumpe | `auto` |
  | `cc SetMode` | `w`, ein Feld, `hwcmode` | Betriebsart setzen (B505 `02`) | — |
  | `cc Mode` Feld 4 | `r`, `hwcmode` | **Teleswitch-Betriebsart**, siehe unten | `off` |
  | `cc Status0a` Feld 3 | `r`, `onoff` | Pumpenzustand | `off` |
  | `cc Timer_Monday..Sunday` | `r;w` | Zeitprogramm, drei Fenster je Tag | alle leer |
  | `hwc CirPump2` | `r`, `onoff` | Zustand der Zirkulationspumpe | `off` |

  **Der Regler schaltet die Pumpe derzeit nie von selbst.** Die Betriebsart
  steht zwar auf `auto`, aber alle sieben Zeitprogramme sind auf Fenster der
  Länge null gesetzt (`10:20;10:20;16:00;16:00;22:00;22:00`) — passend dazu,
  dass die Zirkulation an dieser Anlage bisher extern gesteuert wird. **Die
  Pumpe hängt derzeit nicht am ZP-Ausgang** (vom Nutzer bestätigt, 2026-09-03);
  die Verdrahtung folgt, der Regelweg steht.

  **Der Regler führt das Einzelfeld-Register 2B00 sehr wohl — ebusd kennt es
  für `cc` nur nicht.** `23.solsy.cc.csv` bindet allein `hwcmode.inc` ein, also
  fehlt der Lesename. Über `hex` (im Add-on freigeschaltet am 2026-09-03) ist
  es direkt lesbar:

  | Rohbefehl | Antwort | Bedeutung |
  |---|---|---|
  | `hex 23b509030d2b00` | `0103` | Betriebsart `auto`, deckt sich mit `cc Mode` Feld 2 |
  | `hex 23b509030d3200` | `013c` | 30,0 °C, deckt sich mit `cc Mode` Feld 1 |
  | `hex 23b509030d3f00` | `0102` | Teleswitch-Betriebsart `off` |
  | `hex 26b509030d2b00` | `0102` | Gegenprobe: `hc OperatingMode` = `off` |

  Geschrieben wird trotzdem über `cc SetMode` — eine eigene CSV wäre ein Fork
  der Community-Konfiguration, und `define` ist im Add-on nicht freigeschaltet.
  Das ist unbedenklich: `SetMode` trägt genau ein Feld (Invariante 1 bleibt
  gewahrt) und hat denselben Datentyp wie das gelesene Feld, verliert also
  keine Auflösung. Die Ausnahme von Invariante 2 steht benannt in
  `const.WRITE_EXCEPTIONS`.
- **Das vierte Feld der Sammelnachricht `Mode` ist die Teleswitch-Betriebsart.**
  Am 2026-09-03 gegengelesen: `hwc Mode` Feld 4 = `off` = `hwc
  TeleswitchOperatingMode2`, `mc Mode` Feld 5 = `low` = `mc
  TeleswitchOperatingMode`. Die Vermutung in `water_heater.py`, dort stehe die
  Zirkulation, war falsch und ist korrigiert. Für die Schreiblogik ändert das
  nichts — die Sammelnachricht wird ohnehin nie beschrieben.
- **Die archivierte CSV trägt auch die Bezeichnungen der Messwerte.** Nicht nur
  die Betriebsarten (siehe unten) -- jede Zeile in `archived/de/vaillant/*.csv`
  führt den deutschen Namen aus dem Reglermenü. Daraus stammen seit dem
  2026-09-03:

  | Register | CSV-Bezeichnung | Name in Home Assistant |
  |---|---|---|
  | `hc/mc FlowTempDesired` | Vorlaufsolltemperatur | Vorlaufsolltemperatur |
  | `mc FlowTemp` | VF2 Sensor | Vorlauftemperatur |
  | `hc FlowTempMax` | Max. Vorlauftemp. | Maximale Vorlauftemperatur |
  | `mc FlowTempMax` | Maximaler Vorlaufsollwert | Maximale Vorlauftemperatur |
  | `ui BoilerHoursB1` | **Ansteuerstunden Gerät 1** | Ansteuerstunden Wärmeerzeuger |
  | `sc SolFlowRate` | Volumenstrom Solarkreis | Volumenstrom |
  | `hc SumFlowSensor` | VF1 | Sammelvorlauf |
  | `sc Coll1Sensor` | KOL1 Sensor | Kollektor |

  Wörtlich übernommen wird sie nicht überall, und mit Grund: `VF1`, `VF2
  Sensor` und `KOL1 Sensor` sind Klemmenbezeichnungen, keine Begriffe. Der
  Oberflächenname sagt, was gemessen wird -- dieselbe Linie wie beim
  Sammelrücklauf. Wo die CSV dagegen einen echten Begriff führt, gilt er:
  `BoilerHoursB1` zählt **Ansteuerstunden**, also die Zeit, die der Regler
  Wärme angefordert hat, und nicht die Laufzeit eines Kessels. An dieser
  Anlage, deren Brenner abgeschaltet ist, war „Betriebsstunden Kessel" die
  falsche Auskunft.

  Nicht belegbar bleiben die fünf Solar-Grenzwerte und `FlowTempMax` als
  Menütexte: sie stehen in der Fachhandwerkerebene, und die
  Installationsanleitung liegt nur als Bild vor.
- **Ein Fühler, drei Entitäten.** `Storage1Sensor2` erscheint als Zustand des
  `water_heater`, als Sensor „Speichertemperatur" am Warmwasser und als
  „Speicherfühler 1 (oben)" am Solar. Der dritte ist gewollt (die
  Solarschichtung braucht ihn neben Fühler 2), der zweite ist eine Dopplung des
  ersten. Er bleibt, weil ein Entfernen die Statistik wegwirft; wer neu
  aufsetzt, braucht ihn nicht.
- **Die Betriebsarten heißen am Bedienteil anders, als der Datentyp nahelegt
  — und zwar je Kreis verschieden.** Bedienungsanleitung 0020094390:

  | ebusd | Heizkreise (Tab. 3.2) | Warmwasser + Zirkulation (Tab. 3.3) |
  |---|---|---|
  | `auto` | Auto | Auto |
  | `on` | **Heizen** | **Ein** |
  | `eco` | Eco | — |
  | `low` | **Absenken** | — |
  | `off` | Aus | Aus |

  Die Grundanzeige des Reglers belegt es wörtlich: `HK1 Heizen 22 °C`,
  `Etage1 Eco 20 °C`, `Speicher Auto 60 °C`. Bis zum 2026-09-03 stand in Home
  Assistant „Zeitprogramm / Dauerbetrieb / Absenkung" — eine Beschriftung, die
  nirgends am Gerät auftaucht. Ebenso die beiden Sollwerte: das Menü nennt sie
  „Raumsolltemperatur" und „Absenktemperatur", nicht „Raumsoll Tag" und
  „Raumsoll Absenkung".

  Dass `on` in zwei Kreisen verschieden heißt, löst `translation_key` in der
  Beschreibung: der `key` bleibt und mit ihm die `unique_id`, nur die
  Beschriftung weicht ab (`circulation_mode`, `circulation_mode_state`).

  **Der Warmwasserkreis bot zwei Betriebsarten zu viel an.** `hwc
  OperatingMode2` ist in der ebusd-Konfiguration als `mcmode` deklariert und
  nähme `eco` und `low` an; Tab. 3.3 kennt für Warmwasser und Zirkulation aber
  nur Auto, Ein und Aus. Die `operation_list` des Speichers ist deshalb seit
  dem 2026-09-03 auf diese drei gekürzt.

  Nicht abbildbar bleibt eine Anzeige des Bedienteils: läuft das
  Ferienprogramm, zeigt der Regler **„Urlaub"** anstelle der Betriebsart und
  lässt sie nicht verstellen. Unsere Entitäten zeigen weiter die darunter
  liegende Betriebsart. `hc IsInHoliday` (2700) wäre der Weg dorthin, ist aber
  nicht eingebunden.
- **Ein kalter Zwischenspeicher sah aus wie ein fehlender Fühler.** Am
  2026-09-03 fehlten nach einem gemeinsamen Neustart von ebusd und Home
  Assistant **15 Entitäten** — neun ganz, sechs als `restored` in der
  Registry —, während ebusd für jedes der 42 Register einen gültigen Wert
  hatte und `poll: 41` meldete. Ursache ist das Zusammentreffen zweier
  Eigenschaften, die je für sich richtig sind: die Poll-Anmeldung scheitert für
  einzelne Register, solange ebusd noch scannt (sie wird im Hintergrund
  nachgeholt), und eine Entität entsteht nur dort, wo **beim Setup** ein Wert
  vorliegt — was ein nicht angeschlossener Fühler (`cutoff`) verhindern soll.
  Der Nachbau der Anmeldung kommt für die Entitäten also zu spät; sie entstehen
  genau einmal.

  Behoben mit `AuromaticCoordinator.async_warm_cache()`, aufgerufen zwischen
  dem ersten Abruf und dem Anlegen der Plattformen: was danach noch fehlt, wird
  einzeln mit `read -m` nachgeholt. Das kostet im Normalfall nichts (es fehlt
  nichts) und im Fehlerfall einen Buszugriff je Register. Antwortet ebusd gar
  nicht, bricht die Schleife nach drei Fehlversuchen ab, statt den Setup
  minutenlang in Zeitabläufe laufen zu lassen.

  Die Diagnose ging über die Zustandsattribute: `"restored": true` unterscheidet
  eine Registry-Leiche von einer Entität, die nur gerade keinen Wert hat.
- **Kein Telefonschalter, und keiner geplant.** `sc TeleSwitch` stand vom
  2026-09-01 bis 2026-09-03 unverändert auf `off` — an dieser Anlage ist der
  Eingang nicht belegt. Der Binärsensor „Telefonschalter" zeigte damit eine
  Funktion, die es nicht gibt; er ist am 2026-09-03 samt Poll-Eintrag
  entfallen. Der gleichnamige Eingang des Warmwasserkreises (`hwc TeleSwitch`)
  war ohnehin nie eingebunden, ebenso wenig die beiden
  `TeleswitchOperatingMode`-Register.
- **Keine brauchbare Raumtemperatur.** `ui RoomTemp` liefert zwar gültige Werte
  (~30 °C), das Bedienteil hängt aber im Heizungsraum. Als Führungsgröße
  bestätigt unbrauchbar.
- **`ui StateEM` und `ui DesiredDegreeB1..8` antworten nicht** (`no data
  stored`). Systemzustand kommt stattdessen aus `ui SystemModeStream1`.
- **Ertragsstatistik:** `ui YieldThisYear` / `YieldLastYear`, je zwölf
  Monatswerte in kWh. Nullwerte April–Juni 2026 sind echt (Heizung während des
  Verkaufs abgeschaltet), kein Dekodierfehler. Fortgeschrieben wird einmal am
  Tag: am 2026-09-02 um 00:04 buchte der Regler die 6 kWh des Vortages
  (603 → 609), davor und danach stand die Zahl still.
  **Zwölf Felder heißen zwölf Telegramme.** Das erklärt den Ausreißer aus der
  Busaufnahme: `YieldLastYear` kam auf 36 Anfragen = drei Abrufe mal zwölf
  Monate, also genau die Erwartung für Stufe 9 — kein Prioritätsfehler. Beide
  Register zusammen machten rund ein Fünftel des gesamten Verkehrs aus. *Offen
  bleibt allein, warum `YieldThisYear` mit 72 Anfragen auf sechs statt drei
  Abrufe kam.*
  Dazu kam ein sichtbarer Fehler: während eines solchen mehrteiligen
  Lesevorgangs hat die Nachricht keinen Wert, `find` liefert sie nicht, und
  die Entität fiel für einen Abrufzyklus auf `unavailable` — am 2026-09-02
  sechsmal in 16 Stunden, jedes Mal exakt im 19-Minuten-Raster der Stufe 9.
  Beides ist behoben: die zwei Register stehen seit dem 2026-09-02 in
  `READ_MAXAGE` statt in der Warteschlange (einmal je Stunde per `read -m`,
  aus dem Zwischenspeicher beantwortet), und der Koordinator überbrückt eine
  fehlende Nachricht bis zu zehn Minuten lang mit ihrem letzten Wert. Die
  Frist ist der Punkt: was länger ausbleibt, wird `unavailable` und
  protokolliert — sonst versteckte die Überbrückung genau den Ausfall, den
  der Poll-Satz verhindern soll.

---

## 2. Was gebaut ist

`custom_components/auromatic/` — Phase 2 vollständig, Phase 3 teilweise.

| Modul | Inhalt |
|---|---|
| `ebusd.py` | Asynchroner TCP-Client für Port 8888, `parse_field`, Filterlogik |
| `coordinator.py` | `DataUpdateCoordinator`, ein `find` pro Kreis je Intervall |
| `poll.py` | Welche 40 Register ebusd aktiv vom Bus holen soll, in drei Prioritäten |
| `entity.py` | `CircuitMixin` + Basisklasse, Gerätezuordnung per `via_device` |
| `config_flow.py` | Einrichtung inkl. Adress-Suche, Options-Flow für das Intervall |
| `sensor.py` | Temperaturen, Erträge, Laufzeiten, Systemzustand, Solar-Grenzwerte |
| `binary_sensor.py` | Störung, Kollektor- und Zirkulationspumpe, Frost- und Kollektorschutz |
| `select.py` | **Betriebsart** für `hc`, `mc` und `cc` — der zentrale Steuerhebel |
| `number.py` | Raumsollwerte Tag/Absenkung, Heizkurve, Solarhysterese |
| `water_heater.py` | Speicher: Ist, Soll, Betriebsart |
| `diagnostics.py` | Vollständiger Registerbestand plus `ebusctl info`, Host redigiert |
| `strings.json`, `translations/` | Alle Oberflächentexte, englisch und deutsch |
| `icons.json` | Icons, nur wo keine `device_class` eins liefert |

Ein HA-Gerät je Bus-Adresse, alle per `via_device` am Regler.

### Verifiziert

- ebusd-Protokollschicht gegen wortgetreue Antwortdaten der Anlage
  (`tests/test_ebusd.py`, 69 Prüfungen).
- Vollständigkeit von Übersetzungen und Icons gegen den Entitätsbestand
  (`tests/test_translations.py`, 497 Prüfungen).
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
  `poll: 40` (der damalige Satz), `scan: finished`, keine Warnung im
  Protokoll, 39 Entitäten ohne eine einzige `unavailable`. Über acht Minuten
  gemessen liegt der Abstand
  zwischen zwei Messwerten bei **120–180 s** (Kollektor, Ertragsfühler,
  Außentemperatur) — im Raster des 60-Sekunden-Abrufs also rund 135 s, gegen
  **935 s** vorher. Siebenfach frischer bei unveränderter Buslast: Symbolrate
  32 von 166 möglichen, vorher 23 von 183.
- **Die drei Prioritätsstufen am Bus nachgezählt** (2026-09-01, `grab result
  all` gegen den laufenden Satz, `poll: 38`, Symbolrate 38 von 167): je
  Register 20–21 Anfragen auf Stufe 1, genau 7 auf Stufe 3, genau 3 auf
  Stufe 9 — entworfen waren 20 : 6,7 : 2,2. 37 der 38 Register erscheinen als
  eigene Anfrage von `0x31`, das 38. (`hc SumFlowSensor`) kommt passiv herein.
  Die mitgelesenen Antwortbytes stehen dabei sämtlich auf den Ausgangswerten
  der Schreibtests: `hc` 25,0 / 18,0 / 1,00 / `off`, `mc` 22,0 / 19,0 / 0,50 /
  `off`, `mc FlowTempMax` 40, `hwc` 50,0 / `auto`, `sc` 12 / 5 K.

- **Ein Tag im Betrieb** (2026-09-02, 16 h ohne Unterbrechung seit dem letzten
  HA-Neustart, gelesen über die REST-API): `poll: 38`, `scan: finished`,
  `reconnects: 0` — der Koordinator musste kein einziges Mal nachmelden. Alle
  39 Entitäten mit gültigem Wert. Messwerte im Median alle **120 s**
  (Kollektor, TD1, Solarrücklauf, Speicher unten), Sammelvorlauf alle 30 s.
  Einzige Ausfälle: die beiden Ertragssensoren, je einen Zyklus lang, sechsmal
  — Ursache gefunden und behoben, siehe oben. Die Kollektorpumpe taktete
  achtmal in 1¾ Stunden bei 82 °C Speicher oben; kurz vor Ladeschluss ist das
  normal.
- **Abrufintervall: 15 s** (statt der 60 s Voreinstellung, in den Optionen
  gesetzt). Ablesbar am Zeitraster aller Zustandswechsel — 105 / 120 / 135 /
  225 s, alles Vielfache von 15. Bewusst so belassen: der Bus merkt davon
  nichts, `find` liest nur den Zwischenspeicher. Seit dem 2026-09-02 stehen
  die Optionen auch in den Diagnosedaten, damit das nächste Mal niemand
  wieder raten muss.

- **Die fünf Solar-Grenzwerte am Gerät gesehen** (2026-09-03, knapp zwei
  Stunden nach dem HA-Neustart mit dem neuen Satz, gelesen über die REST-API
  von HA 2026.9.0). Alle fünf Register antworten und tragen plausible Werte:
  `SolProtectionStartTemp` 130 °C, `ScProtectionHysteresis` 30 K,
  `SolHwcMaxLoadTemp1` 90 °C, `KolTempMin1` 0 °C, `SolFlowRate` 3,50.
  Damit ist auch die **Einheit von `SolFlowRate` bestätigt**: die Entität zeigt
  3,5 l/min, wie die aufgelöste CSV es mit `UIN` und Teiler 60 vorgibt; die
  TypeSpec-Fassung mit `flowrate` in l/h ist für diese Anlage die falsche.
  43 Entitäten, keine einzige `unavailable`, Messwerte zuletzt vor 113–368 s
  aktualisiert. `scan: finished`, `reconnects: 0`.
- **`poll: 43` statt der erwarteten 39** -- und das ist richtig so. Die Liste
  kann zur Laufzeit nur wachsen (siehe `poll.py`): ebusd lief seit der
  Anmeldung des alten 38er-Satzes durch, die fünf neuen Register kamen dazu,
  gestrichene bleiben bis zum nächsten ebusd-Neustart stehen. Der Koordinator
  vergleicht mit `polled >= expected` und meldet deshalb nicht in jeder Runde
  neu an. Wer die 39 sehen will, muss ebusd neu starten, nicht Home Assistant.
- **Der Schreibweg des Zirkulationskreises am laufenden Regler** (2026-09-03,
  direkt über die Kommandoschnittstelle von ebusd, Roundtrip mit
  Wiederherstellung):

  | Schritt | `cc Mode` Feld 2 | 2B00 roh | `hwc CirPump2` | `cc Status0a` |
  |---|---|---|---|---|
  | Ausgang | `auto` | `0103` | `off` | `-;off;off;off;0` |
  | `write -c cc SetMode on` | `on` | `0101` | **`on`** | `-;off;on;on;0` |
  | `write -c cc SetMode off` | `off` | — | `off` | — |
  | `write -c cc SetMode auto` | `auto` | `0103` | `off` | — |

  Drei Dinge sind damit belegt: der ZP-Ausgang folgt dem Befehl ohne
  Verzögerung, `SetMode` schreibt tatsächlich das Register 2B00, und
  `hwc CirPump2` ist die richtige Rückmeldung dafür. Die Anlage stand danach
  wieder auf dem Ausgangswert. Was der Test **nicht** zeigt, ist die
  Verdrahtung: die Pumpe hängt derzeit nicht am Ausgang.

- **Der vollständige Bestand nach dem Umbau** (2026-09-03, nach dem Neustart
  mit `async_warm_cache`): **45 Entitäten, keine einzige ohne Wert** -- weder
  `unavailable` noch `restored`. Alle 42 Register des Poll-Satzes tragen einen
  Wert, `scan: finished`, `reconnects: 0`, kein einziger Protokolleintrag zur
  Integration. Die Gerätezuordnung steht wie entworfen: sechs Anlagenwerte am
  Regler, einer am Bedienteil, drei an der Zirkulation, achtzehn am Solar.
  Die sieben Entitäten, die beim Neustart neu entstanden sind, haben genau die
  vorhergesagten `entity_id` bekommen -- Home Assistant bildet sie aus Bereich,
  Gerät und Entitätsname.
- **Der Regler schaltet seine Ausgänge 1,5 s nach der Quittung.** Am
  2026-09-03 über beide Richtungen gemessen, ein Lesevorgang je Runde: nach
  `write -c cc SetMode on` stand `hwc CirPump2` bei +1,44 s noch auf `off` und
  bei +1,59 s auf `on`; zurück bei +0,95 s noch `on`, bei +1,60 s `off`. Der
  Schreibbefehl ist also längst quittiert, bevor das Relais fällt.

  Das ist der Grund für `EFFECT_SETTLE` im Koordinator. Der Pumpenzustand
  hinkte dem Schalten vorher um bis zu zwei Minuten nach -- gemessen 72 s bis
  in den Zwischenspeicher von ebusd, 82 s bis in die Oberfläche --, weil er
  eben nicht in dem Register steht, das ihn schaltet, sondern in einem anderen
  Kreis auf Stufe 1 der Warteschlange. `effect_message` in der Beschreibung
  nennt dieses Register, `async_write` liest es nach dem Schreiben mit; siehe
  Invariante 7.

  **Ohne die Wartezeit wäre das Nachlesen schlimmer als nutzlos gewesen:** es
  holt den alten Wert frisch vom Bus und schreibt ihn damit in den
  Zwischenspeicher von ebusd, wo er bis zum nächsten Durchlauf der
  Warteschlange stehen bleibt. Genau so verhielt sich der erste Anlauf am
  2026-09-03, und es fiel nur auf, weil der Test den Regler selbst gegengelesen
  hat statt nur Home Assistant.

  *Drei Messungen davor waren wertlos und sind es wert, erwähnt zu werden: die
  erste, weil das eigene `read -f` den Zwischenspeicher aufgefrischt hatte,
  bevor Home Assistant gefragt wurde; die zweite, weil er noch auf dem Wert der
  ersten stand; die dritte, weil Home Assistant mitten im Test neu startete
  (43 Entitäten mit identischem `last_changed` verraten das). Wer die Latenz
  eines Zwischenspeichers misst, darf ihn nicht selbst anfassen -- und muss
  wissen, wann das System zuletzt hochgefahren ist.*
- **Das Nachlesen der Wirkung am laufenden System** (2026-09-03, nach dem
  Neustart mit `EFFECT_SETTLE`, gemessen mit gesetztem System und in der
  Reihenfolge „erst Home Assistant fragen, dann den Regler gegenlesen"):

  | Aktion | Dauer | HA-Pumpe | Regler | erwartet |
  |---|---|---|---|---|
  | `select_option: on` | 2,9 s | `on` | `on` | `on` |
  | `select_option: auto` | 2,5 s | `off` | `off` | `off` |

  Die Oberfläche stimmt in beiden Richtungen unmittelbar nach dem
  Dienstaufruf mit dem Regler überein -- vorher waren es bis zu zwei Minuten.
  Dazu im selben Durchgang: 48 Entitäten ohne eine einzige ohne Wert, 42 von
  42 Registern mit Wert im Zwischenspeicher, kein Protokolleintrag zur
  Integration, 22 Dashboard-Referenzen ohne Leiche.
- **Der Schreibweg der Zirkulation durch Home Assistant selbst** (2026-09-03),
  nicht mehr nur über `ebusctl`: `select.select_option` auf `on` setzt das
  Rohregister 2B00 auf `0101`, Auswahl und Reglerzustand folgen sofort, das
  Zurücksetzen auf `auto` stellt `0103` wieder her. Kein Zurückspringen der
  Oberfläche -- `write_and_confirm` tut, was es soll.

### Nicht verifiziert

Vorausgesetzt wird **HA 2024.6+** (wegen `entry.runtime_data`); getestet auf
**HA 2026.9.0**. Nicht durchgespielt ist der Weg über die Oberfläche selbst --
geprüft wurde über Dienstaufrufe, die dieselben Entitätsmethoden ausführen.

**`mc RoomTempOffset` hat derzeit keine Entität.** Erwartet waren 44
Entitäten (46 Beschreibungen minus `sc Coll2Sensor` und `sc Storage3Sensor3`,
beide `cutoff`), gezählt sind 43. Das Register ist nur schreibend definiert und
steht deshalb in `POLL_EXEMPT`; einen Wert hat es nur, solange ebusd einen
Schreibvorgang des Bedienteils passiv mitgehört hat. Am 2026-09-01 lag einer
vor, am 2026-09-03 nicht mehr -- vermutlich hat ein Rescan das
Nachrichtenobjekt samt Zwischenspeicher ersetzt. Entitäten entstehen nur, wo
beim Setup ein Wert vorliegt, also fehlt sie seither. Sie kommt beim nächsten
HA-Neustart wieder, wenn das Bedienteil bis dahin den Offset geschrieben hat.
Kein Fehler im Poll-Satz: die anderen 43 sind vollständig und frisch.

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
wenn das Bedienteil ihn setzt. Es steht deshalb in `POLL_EXEMPT`.

Beides führte zur heutigen Lösung: die Anmeldung wartet auf `scan: finished`,
fasst im Hintergrund nach — und vor allem prüft der Koordinator bei **jedem**
Abruf, ob die Poll-Liste noch die erwartete Länge hat. Ein Zeitplan genügt
nicht, weil ein Rescan die Einträge jederzeit entfernt und `scan` auf dieser
Anlage immer wieder kurz auf `running` springt. Die Länge der Liste ist das
einzige verlässliche Signal; sie kostet ein `info` je Abruf.

**Ein abwesender Kreis ist ein Zustand, kein Sonderfall.** Der Kessel war
jahrelang stromlos und kann es wieder sein. ebusd lädt seine CSV dann gar
nicht, und `find -c bai` antwortet nicht etwa leer, sondern mit `ERR: element
not found`. Am 2026-09-05 zeigte sich, dass gleich drei Mechanismen daran
vorbeiliefen — jeder für sich lautlos:

Der Koordinator setzte die letzten Werte des ausgefallenen Kreises im
Ausnahmezweig selbst wieder ein. `carry_forward` überspringt aber jedes
Register, das bereits in der Antwort steht: die Frist von 600 s begann nie zu
laufen, und der Kessel hätte bis in alle Ewigkeit „Flamme an, 1,46 bar, keine
Störung" gemeldet. Also genau der Ausfall, gegen den die Überbrückung
überhaupt befristet ist.

`async_warm_cache` zählte die sofortige Fehlerantwort wie einen Zeitablauf und
gab nach dreien auf. Weil `bai` in `POLL_SET` vorn steht, wären beim kalten
Start die Entitäten *aller übrigen* Kreise ausgeblieben — dieselben, für die
es diesen Schritt seit dem 2026-09-03 gibt.

Und `_ensure_polled` verglich die Poll-Liste mit allen 51 Registern. Elf davon
sind ohne Kessel nicht anmeldbar; der Vergleich wäre nie aufgegangen, jede
Minute hätte eine vollständige Neuanmeldung angestoßen und alle paar Minuten
hätte „Poll-Satz nach 6 Versuchen unvollständig" im Protokoll gestanden — die
eine Warnung, die einen echten Verlust der Liste anzeigen soll.

Behoben in 0.3.1: der Koordinator führt mit, welche Kreise ebusd gerade nicht
kennt. Das Signal fällt beim Abruf ohnehin an — ein Kommandofehler bei `find`
heißt „abwesend", eine Antwort heißt „wieder da". Das Soll des Poll-Satzes
rechnet nur über die bekannten Kreise, aufs Aufgeben zählen nur noch echte
Zeitabläufe, und der ausgefallene Kreis wird leer übergeben, damit die Frist
greift. Kommt der Kessel zurück, wächst das Soll von selbst wieder auf 51 und
der nächste Abruf meldet seine elf Register nach.

Nachgezogen im Fake-ebusd: er gab für einen unbekannten Kreis bislang eine
leere Liste zurück statt der Fehlerzeile. Der Fall, an dem alles hängt, war
damit gar nicht prüfbar.

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
Daraus drei Stufen in `poll.py`: 19 Messwerte auf 1, fünf Bedienelemente
(Betriebsarten der drei Kreise, Warmwassersollwert und -betriebsart) auf 3, 27
Sollwerte und Zähler auf 9. Sollwerte brauchen die Warteschlange kaum, weil
`write_and_confirm` sie nach jeder Änderung ohnehin mit `read -f` frisch holt;
sie stehen nur drin, falls jemand direkt am Regler dreht — die Bedienelemente
deshalb in der Mitte, weil dieser Fall bei ihnen der wahrscheinlichste ist.
Rechnerisch: ~2,4 Minuten für einen Messwert, ~7,1 für ein Bedienelement, ~21
für einen Sollwert, bei unveränderter Buslast.

Dass die Buslast dabei wirklich unverändert bleibt, ist der Grund, warum die
fünf Solar-Grenzwerte (2026-09-02) in die Warteschlange gewandert sind und
nicht in `READ_MAXAGE`: ebusd pollt eine Nachricht je Takt, ob die Liste
dreißig Einträge hat oder vierzig. Ein `read -m` dagegen geht nach Ablauf des
Höchstalters *zusätzlich* auf den Bus. Fünf Register auf Stufe 9 kosten
deshalb nichts als drei Sekunden Latenz für die übrigen: 17,11 Anteile wurden
am 2026-09-02 zu 17,67, ein Messwert kam statt alle 103 nun alle 106 Sekunden
dran. Seit dem Wegfall des Telefonschalters und dem Hinzukommen des
Zirkulationskreises (beides 2026-09-03) waren es 18,89 — ein Messwert alle
113 Sekunden.

Das Modell ist nachgemessen: mit den 21,11 Anteilen des ersten Satzes sagte es
127 s voraus, der Median über einen vollen Tag lag bei 120 s.

Der Kessel hat den Satz am 2026-09-04 auf **23,67 Anteile** verbreitert: vier
Register auf Stufe 1 (`WaterPressure`, `Currenterror`, `Flame`, `Status01`)
und sieben auf Stufe 9 (Statuscode und sechs Zähler). Ein Messwert kommt
seither rechnerisch alle 142 statt alle 113 Sekunden — knapp eine halbe Minute
träger, für elf Register. Das ist der Preis, und er ist bewusst bezahlt: ohne
Wasserdruck, Flamme und Fehlerspeicher des Brenners bleibt genau die Störung
unsichtbar, die diese Anlage am 2026-09-04 hatte. Eines der vier auf Stufe 1
kostet dabei vermutlich gar nichts — `bai Status01` fragt das Bedienteil
ohnehin alle 17 Sekunden ab, und was frisch im Zwischenspeicher liegt,
überspringt der Poll (derselbe Effekt wie bei `hc SumFlowSensor`).

Vier Register bleiben mit Grund draußen (`POLL_EXEMPT`): `mc RoomTempOffset`
ist nur schreibend definiert, `sc Coll2Sensor` und `sc Storage3Sensor3` melden
`cutoff` und haben deshalb gar keine Entität — sie würden je einen der
schnellen Plätze für nichts belegen. Dazu `bai SetMode`: in `hcmode.inc` als
`uw` deklariert, also passiv mitgelesen und schreibend, ohne Lesevariante.
ebusd nimmt eine Anmeldung darauf zwar an und sendet dafür keine eigene
Anfrage (in der Busaufnahme danach kein einziges `3108b510`-Telegramm) — der
Eintrag wäre aber bestenfalls wirkungslos und schlimmstenfalls ein
Schreibtelegramm an den Brenner, denn der Master-Teil dieser Nachricht *ist*
der Stellbefehl. Frisch bleibt sie ohnehin, das Bedienteil schickt sie alle
17 Sekunden. Jede Ausnahme macht die übrigen schneller.

Nicht eingebunden ist `bai Errorhistory`, obwohl dort F.75 steht: die Nachricht
trägt ein Feld im Master-Teil (den Index des Eintrags), und `read -p 9 -c bai
Errorhistory` antwortet mit `ERR: end of input reached`. Ohne Poll fröre der
Wert auf dem Stand des letzten Abrufs ein, und eine Entität, die eine alte
Störung als aktuelle zeigt, ist schlimmer als keine. Nachzusehen von Hand:
`ebusctl read -c bai -i 0 Errorhistory`.

Zwei weitere stehen in `READ_MAXAGE` und damit in keiner Warteschlange: die
beiden Ertragsregister sind zwölf Felder breit, kosten also zwölf Telegramme
je Abruf, und ändern sich einmal am Tag. Der Koordinator holt sie stattdessen
selbst mit `read -m 3600` — ebusd beantwortet das aus dem Zwischenspeicher und
geht höchstens stündlich dafür auf den Bus. Damit hat jedes gelesene Register
genau einen von drei Plätzen: Warteschlange, Ausnahme oder Selbstabholung;
`tests/test_translations.py` prüft das.

**Am Regelwerk von Home Assistant ausgerichtet** (Integration Quality Scale).
Umgesetzt: `has-entity-name`, `entity-unique-id`, `runtime-data`,
`test-before-setup`, `parallel-updates` (lesende Plattformen 0, schreibende 1),
`entity-unavailable`, `action-exceptions`, `entity-device-class`,
`entity-category`, `diagnostics`, `entity-translations`, `icon-translations`.
Offen bleiben allein die Punkte, die eine HA-Testumgebung voraussetzen
(`config-flow-test-coverage`, `test-coverage`) und die Platin-Stufe.

**Bedienelement oder Konfiguration.** Ein schreibbarer Wert ohne
`entity_category` steht gleichrangig neben dem, was man täglich anfasst. Das
sind an dieser Anlage genau drei Größen: die beiden Raumsollwerte und die
Betriebsart. Alles andere ist Auslegung -- die **Heizkurve** wird einmal
eingestellt und nicht nach Bedarf gedreht, die beiden Solar-Schaltdifferenzen
erst recht. Seit dem 2026-09-03 tragen alle vier `EntityCategory.CONFIG`.

Bei den Schaltdifferenzen stand sie schon vorher da, aber im gemeinsamen
`_DIFF`-Bündel neben Einheit und Schrittweite -- an der Beschreibung nicht
ablesbar, und für `tests/test_translations.py` unsichtbar, das die
Beschreibungen per `ast` liest. Sie steht jetzt bei jeder Entität einzeln, und
der Test verlangt genau das: außerhalb der drei Alltagsgrößen muss die
Kategorie an der Beschreibung selbst stehen.

**Die Diagnose-Kategorie ist für Werte über das Gerät, nicht für Fachdaten.**
Sie steckt Entitäten in eine eingeklappte Gruppe -- richtig für Laufzeitzähler,
geräteseitige Grenzwerte, den abgeleiteten Reglerzustand und den Raumfühler,
der als Führungsgröße unbrauchbar ist. Falsch war sie beim **Solarertrag des
Vorjahres**: dieselbe Fachgröße wie der laufende Ertrag, nur ein Jahr älter,
und der einzige Grund, ihn anzusehen, ist der Vergleich mit ihm. Seit dem
2026-09-03 stehen beide in derselben Gruppe. Der Unterschied zwischen ihnen
gehört in die `state_class` und steht auch nur dort: das laufende Jahr ist ein
Zähler (`TOTAL_INCREASING`, fällt im Januar auf null), das Vorjahr ein
feststehender Wert ohne `state_class` -- beim Jahreswechsel werden alle zwölf
Monatswerte auf einmal ersetzt, und ein Zähler läse darin einen frischen
Ertrag.

**Geräte bilden die Anlage ab, nicht die Busadressen.** ebusd führt jedes
Register unter genau einer Adresse; wo es hingehört, ist damit nicht gesagt.
Sechs Werte gehören der Anlage und nicht einem Kreis -- Außentemperatur,
Sammelvorlauf, Sammelrücklauf, Systemzustand, Störung und die Ansteuerstunden
--, zwei weitere stehen im falschen Kreis: den Solarertrag zählt das
Bedienteil, gesucht wird er beim Solar. Dafür gibt es `device_circuit` in der
Beschreibung. Es steuert **nur** die Gerätezuordnung; `circuit` bleibt, wo es
war, denn es steckt in der `unique_id`, und `source_circuit` bestimmt weiterhin
allein den Leseweg. Drei Felder, drei Fragen: woher lesen, wohin schreiben,
wo anzeigen.

Belegt statt vermutet: `hc OutsideTemp` und `ui OutsideTemp` lieferten am
2026-09-03 zeitgleich 17,81 °C, und `Currenterror` trägt in `hc`, `cc` und `sc`
denselben Inhalt -- es ist der Fehlerspeicher des Reglers, kein Kreiswert.

Zwei Nebenwirkungen, beide bewusst in Kauf genommen: das Gerät „Bedienteil"
behält genau eine Entität (den Raumfühler, der wirklich dort hängt), und der
Heizkreis hat keinen eigenen Messwert mehr -- sein Vorlauf *ist* der
Sammelvorlauf der Anlage (`VF1`, identisch mit `ui FlowTemp`). Und die
`entity_id` folgt der Verschiebung nicht: Home Assistant vergibt sie einmalig
beim Anlegen aus Geräte- und Entitätsnamen. Wer sie geradeziehen will, benennt
sie von Hand um; die `unique_id` und damit die Historie bleiben in jedem Fall.

**Seriennummern nur dort, wo ein Gerät steht.** `DeviceInfo` in Home Assistant
kennt `sw_version`, `hw_version` und `serial_number`; sie zu füllen ist eine
Frage der Wahrheit, nicht der Vollständigkeit. Die fünf Kreise des Reglers
melden im Scan nicht nur dieselbe Artikelnummer, sondern denselben
Produktionszähler (`counter=005114`) — es ist ein Gerät auf fünf Busadressen.
Eine Seriennummer an jedem einzelnen behauptete fünf Geräte, wo eines steht.
Sie steht deshalb am Regler selbst und an den beiden Teilnehmern, die wirklich
eigene Geräte sind: Bedienteil (Ident 0020080465) und Therme. Das
Erkennungsmerkmal im Code ist keine zweite Liste, sondern die Sache selbst: wer
ein eigenes `model` in `const.CIRCUITS` trägt, ist ein eigenes Gerät.

Gelesen wird das mit einem einzigen `scan result` beim Setup — ein Befehl für
alle Adressen, und kein Telegramm auf dem Bus, weil ebusd nur ausgibt, was der
Scan ohnehin ergeben hat. Niemals `scan` ohne `result`: das stößt einen echten
Scan an und wirft die Poll-Liste heraus.

Seit 0.3.2 trägt der Regler dabei seinen **eigenen** Softwarestand (0500) statt
der Version von ebusd. ebusd ist nicht das Gerät, das die Geräteseite
beschreibt; seine Version steht in den Diagnosedaten.

**Beschriftungen folgen dem Bedienteil, nicht dem Datentyp.** Wer bei einer
Störung am Regler steht, soll dort dieselben Wörter lesen wie in Home
Assistant. Maßgeblich ist die Bedienungsanleitung 0020094390; die Einzelheiten
stehen oben unter „Die Betriebsarten heißen am Bedienteil anders". Wo derselbe
Rohwert je Kreis anders heißt, trennt `translation_key` die Beschriftung vom
`key` -- letzterer steckt in der `unique_id` und darf sich nie ändern.

**Icons folgen der `device_class`.** Sie bestimmt Einheit, Darstellung und
Standardsymbol; ein eigenes `icon` steht nur da, wo es keine `device_class`
gibt (Enums, nackte Zahlen, Schalter) oder wo deren Symbol nichts über das
Gerät sagt. Beide Pumpen tragen `RUNNING` und behalten dessen Symbol; ein
`mdi:pump` daneben stand einmal im Plan und ist nie eingebaut worden.
Temperaturen behalten bewusst ihr zustandsabhängiges Thermometer,
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
4. **Thermischer Nachweis der Heizfunktionen — der Nachweis unter Last fehlt
   noch.** Der Lauf vom 2026-09-06 hat den Schreibpfad und den Sammelrücklauf
   entschieden (siehe Abschnitt 1); beides brauchte nur Wärme, keine Abnahme.
   Offen bleibt der Betrieb mit echter Last: an jenem Vormittag nahm niemand
   Wärme ab, der Brenner war nach 90 s am Sollwert und zündete in 33 Minuten
   ein einziges Mal.

   Vor dem nächsten Lauf: die Thermostate der Heizkörper des `hc`-Kreises
   montiert und aufgedreht — nicht alle, die größten Räume genügen als
   definierte Last. Die Fußbodenheizung ist nicht beteiligt (`mc` bleibt aus,
   der Mischer blieb nachweislich zu), ihre alten Thermostatköpfe können also
   warten. Der abgesperrte Kellerkreis ist unkritisch, gehört aber als bekannt
   fehlender Abnehmer ins Protokoll.

   Damit der nächste Lauf die Frage selbst beantwortet, statt sie der
   Auswertung zu überlassen, gelten diese Kriterien für „es gab Abnahme":
   Spreizung über 8 K während des Pumpenlaufs, Pumpe länger als 10 Minuten am
   Stück, mindestens zwei Brennerzyklen, Abkühlrate über 0,3 K/min. Außerdem
   fehlt weiterhin Phase 2, die Sollwertsprünge (Heizkurve, Raumsoll,
   Absenkbetrieb) — sie prüfen die Rechnung des Reglers und brauchen keine
   Last, nur Zeit.
5. **`ui YieldThisYear` wird doppelt so oft abgefragt wie `YieldLastYear`**
   (72 gegen 36 Anfragen bei gleicher Priorität). Beide stehen inzwischen
   außerhalb der Warteschlange, der Punkt ist damit unkritisch — die Frage
   bleibt trotzdem offen.
6. **HACS-Struktur** bewusst zurückgestellt.
7. **Zeitprogramme des Reglers.** 28 Register, vier Kreise mit je sieben Tagen
   (`hc`, `mc`, `hwc`, `cc`; `sc` und `ui` haben keine), alle `r;w` vom Typ
   `timer` — drei Fenster je Tag plus Tagesauswahl. Zurückgestellt, und die
   Darstellung ist der Grund: **eine `schedule`-Entität kann eine Integration
   nicht anlegen.** `homeassistant/components/schedule/manifest.json` führt
   `"integration_type": "helper"`, die Klasse ist `Schedule(CollectionEntity)`
   aus einer Storage-Collection, und die `Platform`-Aufzählung (inzwischen in
   `homeassistant/generated/entity_platforms.py`) kennt kein `SCHEDULE` — es
   gibt also keine Plattform, an die ein Config-Entry weiterreichen könnte.
   Der gangbare Weg wäre `calendar`: eine reguläre Plattform, bei der
   Schreiben ein optionales Feature ist (`CalendarEntityFeature.CREATE_EVENT`),
   also read-only sein darf. Nur ist die Optik eine Kalenderansicht, kein
   Wochenraster. Bis dahin werden die Zeitfenster am Regler selbst gesetzt.
8. **Verdrahtung der Zirkulationspumpe.** Der Regelweg steht und ist geprüft,
   die Pumpe hängt aber noch an ihrer externen Steuerung. Nach dem Rückbau auf
   den ZP-Ausgang ist zu prüfen, ob sie beim Schalten der Betriebsart wirklich
   anläuft — `hwc CirPump2` zeigt nur, was der Regler anfordert. Seit dem
   2026-09-04 ist dabei ein zweiter Anschlusspunkt bekannt: `bai
   AccessoriesOne` (d.27) steht auf `circulationpump`, das Zubehörrelais 1 des
   Kessels ist also ebenfalls als ZP konfiguriert. Welcher der beiden Ausgänge
   verdrahtet ist, entscheidet, wo nachzusehen ist.
9. ~~**`bai HcStarts` zählt nicht, was seine Beschriftung behauptet.**~~
   **Erledigt am 2026-09-13 — die Beschriftung stimmt, die Auflösung war das
   Problem.** Die ebusd-Definition sagt es unmittelbar:

   ```
   r9,bai,HcStarts,d.82 Schaltspiele Heizbetrieb,,08,b509,0d2900,value,s,UIN,-100,,
   ```

   Ein negativer Teiler ist bei ebusd ein Faktor: der Rohwert 2647 wird mit
   100 malgenommen. Die beiden fehlenden Stellen stehen in einem eigenen
   Register, `bai HcUnderHundredStarts` („Heat switch cycles under hundred"),
   und standen am 2026-09-13 auf 2. Der Stand ist also **264 702**, und der
   Zähler steht nicht still — er rührt sich nur alle hundert Starts. Dasselbe
   Paar gibt es für Warmwasser: `HwcStarts` 3400 + `HwcUnderHundredStarts` 2
   = 3402.

   Damit fällt die Beobachtung vom 2026-09-06 in sich zusammen, und die vom
   2026-09-13 dazu: eine ganze Woche samt Wartung, sieben Gebläsestarts, und
   das Hauptregister unverändert. Erwartbar. Aus einem Stillstand über weniger
   als hundert Zyklen folgt nichts.

   Die Entität summiert seit dem 2026-09-13 beide Register (`plus_message` in
   `sensor.py`); beide stehen auf Stufe 9 im Poll-Satz.

   **Was offen bleibt, ist die Größenordnung.** 264 702 Starts auf 7098
   Betriebsstunden sind 37 pro Stunde, also 96 s je Zyklus im Lebensmittel —
   viel, aber für eine Therme ohne Puffer nicht unmöglich. Dagegen steht
   `bai FanStarts` = 50 262, obwohl das Gebläse bei jedem Zyklus mitläuft und
   damit *mehr* Starts haben müsste als der Heizbetrieb allein. Die Erklärung
   ist vermutlich banal: `FanStarts` und `HcPumpStarts` (54 845) sind `UIN`,
   also 16 Bit, und stehen dicht unter der Decke von 65 535. Vier Überläufe
   brächten das Gebläse auf 312 406 und damit über die 268 104 Brennerstarts
   aus Heizen und Warmwasser — stimmig, aber nicht beweisbar, solange niemand
   einen Überlauf beobachtet hat.

   **Praktische Folge: aus Startzählern lässt sich an diesem Gerät kein
   Verhältnis bilden.** Das Stundenargument für die turboTEC-Zuordnung
   (Abschnitt 1, 8174 Lüfter- gegen 7755 Brennerstunden) ist davon nicht
   betroffen — Stundenzähler sind hier weit von der Decke entfernt.
10. **Zustandsanzeigen gehören nicht in die Warteschlange.** `bai Flame` und
   `bai Statenumber` hinken dem Geschehen um 60–90 s bzw. Minuten hinterher
   (Abschnitt 1). Ein Kandidat wäre `READ_MAXAGE` mit kurzem Höchstalter: ein
   `read -m 30` je Abrufzyklus kostet höchstens ein Telegramm pro Minute und
   brächte die Flammenmeldung auf unter 60 s. Zu prüfen ist zugleich, welche
   *anderen* Entitäten schneller veralten, als ihre Anzeige vermuten lässt --
   und welche Beschriftung wie bei `HcStarts` auf einer Annahme statt auf einer
   Messung ruht.
11. **Der Fehlerspeicher des Kessels hat keine Entität — und das hat am
   2026-09-13 zum ersten Mal etwas gekostet.** `bai Errorhistory` lässt sich
   nicht pollen (Master-Feld für den Index). Ein Weg wäre, ihn wie
   `READ_MAXAGE` selbst zu holen — dann allerdings mit `-i`, was der Client
   bislang nicht kennt.

   Die Begründung für das Zurückstellen war, dass `bai Currenterror` die
   anstehende Störung zeigt. Das stimmt und genügt trotzdem nicht: der
   Ringpuffer trug am 2026-09-13 auf den Plätzen 0 und 1 die Fehlernummer
   **70**, wo am 2026-09-04 noch die 75 stand (Plätze 2 bis 9 tragen sie
   weiter). Zwischen beiden Terminen liegt die Wartung vom 2026-09-10. Es sind
   also zwei Ereignisse aufgelaufen, `Currenterror` war zu beiden Zeitpunkten
   leer, und in Home Assistant war davon nichts zu sehen — eine Störung, die
   sich von selbst löst, ist für die Integration bislang nicht passiert.

   Zur Zahl 70 selbst: ebusd hat für das Feld keine Werteliste, es ist ein
   nacktes `UIN`. Die Zuordnung 70 → F.70 („ungültige Gerätevariante", DSN)
   stammt aus der Vaillant-Dokumentation, nicht vom Bus, und ist damit
   schwächer belegt als seinerzeit die 75. Dagegen spricht nichts am Gerät,
   aber es bestätigt sie auch nichts: `DSN` = 5148 = `DSNStart` 5120 +
   `DSNOffset` 28, `ChangesDSN` = 0, `VolatileLockout` = `no`. Ein Zeitstempel
   fehlt wie schon bei der 75 (`-:-`, `-.-.-`), die beiden Einträge lassen
   sich also nicht datieren — nur eingrenzen.

Erledigt am 2026-09-02: die übrigen Solarparameter sind eingebunden (siehe
Abschnitt 1), und die beiden Beschriftungen stehen nicht mehr auf Verdacht --
siehe „Die Reglerbezeichnungen stehen in der archivierten CSV" unten.

Erledigt am 2026-09-03: der Zirkulationskreis ist eingebunden (Gerät
„auroMATIC Zirkulation" mit Betriebsart, Reglerzustand und Pumpenzustand), der
Binärsensor „Telefonschalter" ist ersatzlos entfallen — an dieser Anlage ist
kein Telefonschalter angeschlossen und keiner geplant.

Erledigt am 2026-09-04: der Wärmeerzeuger ist eingebunden. Neues Gerät
„auroMATIC Kessel" (`bai`, 0x08, Modell `Vaillant BAI00, SW 0414 / HW 7401`)
mit elf Entitäten:

| Entität | Register | |
|---|---|---|
| Wasserdruck | `bai WaterPressure` | der Wert, wegen dem der Kessel eingebunden ist |
| Vorlauftemperatur | `bai Status01` Feld 1 | |
| Rücklauftemperatur | `bai Status01` Feld 2 | |
| Vorlaufsolltemperatur | `bai SetMode` Feld 2 | die Anforderung des Reglers |
| Heizungspumpe | `bai Status01` Feld 6 | `overrun` und `hwc` zählen als „läuft" |
| Flamme | `bai Flame` | |
| Heizfreigabe | `bai SetMode` Feld 5 | `disablehc`, umgekehrt gemeldet |
| Störung | `bai Currenterror` | der Fehlerspeicher der Feuerungsautomatik |
| Statuscode | `bai Statenumber` | Diagnose |
| Betriebsstunden / Schaltspiele Heizbetrieb und Heizungspumpe | `HcHours`, `HcStarts`, `PumpHours`, `HcPumpStarts` | Diagnose |
| Stunden bis Wartung, Zündfehler | `HoursTillService`, `DeactivationsIFC` | Diagnose |

Seit dem 2026-09-13 steht der Wartungstermin daneben — allerdings nicht an
der Therme, sondern am Regler: `ui ServicePeriod` ist sein Register, und
gesetzt wird er im Reglermenü. Der Stundenzähler `HoursTillService` (d.84)
bleibt davon unberührt, das sind zwei verschiedene Größen.

Zusammen ergeben die ersten acht die Kette, an der ein F.75 ablesbar wird:
Anforderung liegt an, Freigabe erteilt, Pumpe soll laufen — und der Druck
rührt sich nicht.

Zwei Nachrichten tragen dabei je drei Entitäten (`Status01`, `SetMode`), und
`SetMode` kostet nicht einmal einen Platz in der Warteschlange — dafür haben
seine beiden Entitäten als einzige kein Netz beim Aufwärmen des
Zwischenspeichers, denn gelesen werden darf es nicht.

Das Gerät hieß zunächst „auroMATIC Kessel", dem Muster der übrigen Kreise
folgend, seit 0.3.1 „Kessel" und seit 0.3.2 **„Therme"** — die Bauform ist
belegt, siehe Abschnitt 1. `const.CIRCUITS` kennt dafür zwei optionale Felder,
`model` und `device_name`; alles Übrige leitet `entity.py` weiter aus Name und
Adresse ab. Bestehende `entity_id`s folgen den Umbenennungen nicht; die
Historie bleibt.

Erledigt am 2026-09-06: der **Heizversuch** (Abschnitt 1) hat den Schreibpfad
und den Sammelrücklauf entschieden, dazu vier Nebenbefunde geliefert — die
nicht konstante Kesselüberhöhung, die live rechnende Heizkurve, den
Warteschlangentakt als Grenze für Zustandsanzeigen und einen Ausreißer mit
gültigem Fühlerstatus. Zwei neue offene Punkte sind daraus entstanden (9
und 10), einer ist geschrumpft (4: es fehlt nur noch der Nachweis unter Last).
Gemessen wurde mit `tools/thermal_log.py`, die Rohdaten liegen außerhalb des
Repos.

Ebenfalls am 2026-09-06: die **Geräteseite** trägt jetzt die echten Kenndaten
der drei tatsächlichen Busteilnehmer — Regler, Bedienteil und Therme — mit
Software-, Hardwarestand und Seriennummer aus `scan result`.

Erledigt am 2026-09-13: die **Nachschau nach Wartung und Schornsteinfeger**
(Abschnitt 1). Sie hat zwei Dinge geändert und eines bestätigt.

Geändert: **offener Punkt 9 ist erledigt, und zwar gegen seine eigene
Diagnose** — `bai HcStarts` trägt den Faktor 100, die fehlenden Stellen stehen
in `HcUnderHundredStarts`, und die Entität summiert seither beide. Dazu ist
**`ui ServicePeriod` als Entität dazugekommen** („Wartung", am Regler): der
Termin 10.09.2027 ist der harte Beleg für die Wartung vom 10.09.2026.
`tests/test_ebusd.py` kennt beide als Fixture, `tests/test_translations.py`
prüft das zweite Register des Zählers wie jedes andere gegen den Poll-Satz.

Bestätigt: **der Bestand ist gesund.** Alle 57 damals gelesenen Register
hatten am 2026-09-13 einen Live-Wert, jeder Fühlerstatus `ok`; ohne Wert waren
genau die drei aus `POLL_EXEMPT`, die keine Entität tragen. `poll: 52` gegen
51 angemeldete (der Vergleich ist `>=`), Invariante 3 hält (`mc FlowTempMax`
= 40), die fünf Solargrenzwerte stehen unverändert, und der Solarertrag ist in
sich stimmig: Jahressumme 609 kWh am 2026-09-02, 663 kWh am 2026-09-13, also
+54 kWh in elf Tagen bei 60 kWh im laufenden September.

Verschärft: **offener Punkt 11.** Zwei Einträge im Fehlerspeicher des Brenners
sind aufgelaufen, ohne dass die Integration etwas gezeigt hätte.

## 6. Versionsverwaltung

Git-Repository auf Branch `main`, Remote `origin` auf
<https://github.com/luc-ass/ha-auromatic>. Der Anfangs-Commit enthält den
oben beschriebenen Stand vollständig; veröffentlicht wird über GitHub-Releases,
deren Tag mit dem `version`-Feld in `manifest.json` gleichlauten muss (siehe
README).

Vor Änderungen an der Schreiblogik lohnt ein Blick in `CLAUDE.md`: die dortigen
Invarianten sind aus Fehlern und Anlagenwissen entstanden, nicht aus Vorsicht.
