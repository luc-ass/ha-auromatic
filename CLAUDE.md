# Projektkontext

Custom-Integration für Home Assistant, die eine **Vaillant auroMATIC 620/3**
(Solarheizungsregler, Gerätekennung `SOLSY`) über **ebusd** anbindet.

Ausführlicher Stand, Anlagenfakten und Debugging-Leitfaden: **[STATUS.md](STATUS.md)**.
Bedienung und Installation: [README.md](README.md).

## Aufbau

```
Heizung ── eBUS ── Adapter Shield C6 ── ebusd ── custom_components/auromatic ── HA
                   10.23.10.114:9999   :8888
```

## Tests

```
python3 tests/test_ebusd.py         # 32 Prüfungen, braucht kein Home Assistant
python3 tests/test_translations.py  # 258 Prüfungen, braucht kein Home Assistant
```

`test_ebusd.py` spielt wortgetreue `ebusctl`-Antworten der echten Anlage gegen
einen Fake-ebusd. Neue Protokoll-Eigenheiten gehören dort als Fixture hinein,
nicht als Sonderfall in den Produktivcode.

`test_translations.py` liest die Entitätsbeschreibungen per `ast` aus dem Code
und hält sie gegen `translations/`, `icons.json` und den Poll-Satz aus
`poll.py`. Es fängt genau die Fehler
ab, die sonst still bleiben: eine Entität ohne Namenseintrag erscheint einfach
namenlos, ohne Fehlermeldung. Ebenso geprüft wird, dass jede Schreibnachricht
so heißt wie die zugehörige Lesenachricht (Invariante 2) — ein falscher
Schreibname fällt sonst erst auf, wenn jemand den Wert verstellt.

Wo eine Prüfung Heizkreis und Mischerkreis unterscheiden muss, darf sie nicht
über den Übersetzungsschlüssel gehen: `hc` und `mc` teilen sich denselben
(`key="mode"`), ein Fehler in genau einem Kreis verschwindet dabei.

## Invarianten — nicht ohne Grund ändern

1. **Nie die Sammelnachricht `Mode` schreiben.** Sie enthält acht Felder,
   darunter `floorpavingdryingday` und `floorpavingdryingtemp`. Ein
   Schreibvorgang darauf kann die Estrichtrocknung starten und die verlegte
   Fußbodenheizung tagelang hochheizen. Geschrieben wird ausschließlich auf
   Register mit genau einem Feld.
2. **Die Schreibnachricht heißt wie die Lesenachricht.** `TempDesired`,
   `TempDesiredLow`, `HeatingCurve` und `OperatingMode` sind in der
   ebusd-Konfiguration als `r;w` deklariert — dasselbe Register, einmal lesend
   und einmal schreibend. Die `Set*`-Nachrichten aus `mcmode_inc.tsp` sind
   *nicht* der Weg: es gibt sie nur im Mischerkreis, im Heizkreis antwortet
   ebusd mit `ERR: element not found`, und ihr Datentyp `temp0` kennt nur ganze
   Grad, während das Register selbst (`temp1`) 0,5 K auflöst.
   `tests/test_translations.py` prüft die Gleichheit beider Namen.
3. **`FlowTempMax` bleibt schreibgeschützt.** 40 °C am Mischerkreis sind die
   geräteseitige Absicherung der Fußbodenheizung.
4. **Fühlerstatus auswerten.** ebusd liefert `-19.38;cutoff` für nicht
   angeschlossene Fühler. Ohne Prüfung landen −19 °C als Kollektortemperatur in
   der Statistik.
5. **Doppelte Nachrichtennamen beachten.** `find` listet beide Varianten
   eines `r;w`-Registers unter demselben Namen auf; die Schreibvariante hat
   dabei nie einen Wert. Sie darf den gültigen Lesewert nicht überschreiben.
6. **Der Poll-Satz in `poll.py` muss vollständig bleiben.** Er sagt ebusd,
   welche Register es aktiv vom Bus holen soll. Steht ein Register dort nicht,
   liefert `find` bis in alle Ewigkeit den letzten bekannten Wert — ohne
   Fehlermeldung, ohne `unavailable`. Die Liste lebt nur im Speicher von ebusd
   und wird deshalb bei jedem Abruf nachgehalten: der Koordinator vergleicht
   ihre Größe mit dem Poll-Satz und meldet nur bei Abweichung neu an. Ein
   Zeitplan genügt nicht — ein Rescan von ebusd wirft die Einträge jederzeit
   heraus. `tests/test_translations.py` hält den Satz gegen den tatsächlichen
   Registerbedarf der Plattformen; `POLL_PASSIVE` nennt die Ausnahmen, die
   sich nicht pollen lassen.
7. **Ein Roundtrip pro Kreis** über `find` statt einer Leseanfrage je Register.
   Der eBUS ist langsam; `find` liest nur den Cache von ebusd. Einzige
   Ausnahme: nach einem Schreibvorgang ein `read -f` auf die zugehörige
   Lesenachricht — der Cache steht dort sonst bis zum nächsten Poll auf dem
   alten Wert und die Oberfläche springt zurück. Nur nach Benutzeraktion,
   nie im Abrufzyklus.

## Sprache

Code-Kommentare und Doku auf Deutsch, mit echten Umlauten (`ä ö ü ß`, nicht
`ae oe ue ss`). Bezeichner im Code englisch und ASCII, ebusd-Registernamen
unverändert übernehmen.

**Oberflächentexte stehen nie im Code.** Namen und Zustände kommen aus
`translations/de.json` (deutsch) und `translations/en.json` = `strings.json`
(englische Quelle), Icons aus `icons.json` -- so verlangt es Home Assistant.
Eine neue Entität braucht immer beides, sonst bleibt sie namenlos.
