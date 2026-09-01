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
python3 tests/test_ebusd.py      # 17 Prüfungen, braucht kein Home Assistant
```

Sie spielen wortgetreue `ebusctl`-Antworten der echten Anlage gegen einen
Fake-ebusd. Neue Protokoll-Eigenheiten gehören dort als Fixture hinein, nicht
als Sonderfall in den Produktivcode.

## Invarianten — nicht ohne Grund ändern

1. **Nie die Sammelnachricht `Mode` schreiben.** Sie enthält acht Felder,
   darunter `floorpavingdryingday` und `floorpavingdryingtemp`. Ein
   Schreibvorgang darauf kann die Estrichtrocknung starten und die verlegte
   Fußbodenheizung tagelang hochheizen. Geschrieben wird ausschließlich über
   die Einzelfeld-Nachrichten `SetMode`, `SetTempDesired`, `SetTempDesiredLow`,
   `SetHeatingCurve`.
2. **`FlowTempMax` bleibt schreibgeschützt.** 40 °C am Mischerkreis sind die
   geräteseitige Absicherung der Fußbodenheizung.
3. **Fühlerstatus auswerten.** ebusd liefert `-19.38;cutoff` für nicht
   angeschlossene Fühler. Ohne Prüfung landen −19 °C als Kollektortemperatur in
   der Statistik.
4. **Doppelte Nachrichtennamen beachten.** Lese- und Schreibvariante heißen
   gleich; die Schreibvariante hat nie einen Wert. Sie darf den gültigen
   Lesewert nicht überschreiben.
5. **Ein Roundtrip pro Kreis** über `find` statt einer Leseanfrage je Register.
   Der eBUS ist langsam; `find` liest nur den Cache von ebusd.

## Sprache

Code-Kommentare, Doku und Oberflächentexte auf Deutsch. Bezeichner im Code
englisch, ebusd-Registernamen unverändert übernehmen.
