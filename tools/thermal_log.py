#!/usr/bin/env python3
"""Rekorder für den thermischen Nachweis der Heizfunktionen (Punkt 4).

Schreibt alle 30 s eine CSV-Zeile mit der ganzen Kette vom gerechneten
Sollwert bis zum Rücklauf. Läuft ohne Home Assistant, direkt gegen ebusd.

    python3 tools/thermal_log.py --host homeassistant --out heizversuch.csv

Der Host ist der von *ebusd*, nicht der des Adapters: 10.23.10.114:9999 ist
das Adapter Shield, ebusd selbst läuft als Add-on. Von innerhalb von Home
Assistant heißt es `2ad9b828-ebusd`, von außen braucht es die Adresse des
HA-Hosts und einen im Add-on veröffentlichten Port 8888.

Getippter Text plus Enter landet als Notiz in der nächsten Zeile -- damit
werden die Phasen des Versuchs im Protokoll sichtbar, ohne dass man später
Uhrzeiten zusammensuchen muss. Beenden mit Ctrl-C; die Zählerstände werden
dann wie beim Start noch einmal gesichert.

Zwei Regeln bestimmen die Bauart:

*Ein `find` je Kreis, sonst nichts.* Alles vom Kessel und der Sammelvorlauf
kommen damit umsonst -- das Bedienteil hält sie ohnehin alle 17 bzw. 30 s
frisch, ebusd schneidet mit. Nur die vier trägen Register werden mit
`read -m` nachgeholt, weil die 142 s der Poll-Warteschlange für einen
Sollwertsprung zu grob sind. Vier Telegramme je 30 s liegen unter dem, was
das Bedienteil selbst fährt.

*Auf `bai SetMode` wird nie gelesen.* Der Master-Teil dieser Nachricht ist
der Stellbefehl an den Brenner; ein aktiver Lesevorgang wäre ein
Schreibvorgang. Der Wert kommt ausschließlich aus `find`. Dieselbe
Begründung wie Invariante 1 in CLAUDE.md.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import csv
import datetime as dt
import importlib.util
import os
import pathlib
import signal
import sys

_MODULE = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "auromatic" / "ebusd.py"
_spec = importlib.util.spec_from_file_location("auromatic_ebusd", _MODULE)
ebusd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ebusd)

# Ein find je Kreis deckt alles ab, was ohnehin frisch im Zwischenspeicher
# liegt. 'hwc' ist dabei kein Messwert des Versuchs, sondern die Erklärung
# für Brennerstarts, die nicht zum Heizkreis gehören.
CIRCUITS = ("bai", "hc", "mc", "sc", "hwc")

# Die Register, die in der Warteschlange zu selten drankommen. 30 s Höchstalter
# heißt: ebusd antwortet aus dem Zwischenspeicher, wenn der Wert jünger ist,
# und geht nur sonst auf den Bus.
REFRESH: tuple[tuple[str, str], ...] = (
    ("hc", "FlowTempDesired"),
    ("hc", "OutsideTemp"),
    ("sc", "SumBackflowSensor"),
    ("mc", "FlowTemp"),
)
REFRESH_MAXAGE = 30

# Einmal beim Start und einmal am Ende. Die Differenz prüft nebenbei die sechs
# Diagnosezähler des Kessels gegen die beobachteten Zyklen.
COUNTERS: tuple[tuple[str, str], ...] = (
    ("bai", "HcHours"),
    ("bai", "HcStarts"),
    ("bai", "PumpHours"),
    ("bai", "HcPumpStarts"),
    ("bai", "HoursTillService"),
    ("bai", "DeactivationsIFC"),
)

# Spalte -> (Kreis, Nachricht, Feld, Statusfeld). None heißt: Rohwert.
COLUMNS: dict[str, tuple[str, str, int | None, int | None]] = {
    # Die treibende Größe.
    "aussentemp": ("hc", "OutsideTemp", 0, 1),
    # Station 1: was der Regler rechnet.
    "vorlauf_soll_hc": ("hc", "FlowTempDesired", 0, None),
    # Station 2: was er beim Kessel anfordert.
    "vorlauf_soll_bai": ("bai", "SetMode", 1, None),
    "heizsperre": ("bai", "SetMode", 4, None),
    # Station 3: was der Kessel liefert.
    "kessel_vorlauf": ("bai", "Status01", 0, None),
    "kessel_ruecklauf": ("bai", "Status01", 1, None),
    "kesselpumpe": ("bai", "Status01", 5, None),
    # Station 4 und 5: was die Anlage misst.
    "sammelvorlauf": ("hc", "SumFlowSensor", 0, 1),
    "sammelruecklauf": ("sc", "SumBackflowSensor", 0, 1),
    # Zeugen.
    "flamme": ("bai", "Flame", 0, None),
    "wasserdruck": ("bai", "WaterPressure", 0, 1),
    "statuscode": ("bai", "Statenumber", 0, None),
    "stoerung": ("bai", "Currenterror", None, None),
    # Kontrollen: der Mischer muss zu bleiben, Warmwasser und Solarpumpe
    # erklaeren Ausschlaege, die nicht zum Heizkreis gehoeren.
    "mischer_vorlauf": ("mc", "FlowTemp", 0, 1),
    "speicher_oben": ("hwc", "Storage1Sensor2", 0, 1),
    "solarpumpe": ("sc", "SolCollPumpED1", 0, None),
    # Die Stellgroessen selbst -- damit steht in jeder Zeile, welcher
    # Betriebszustand gerade eingestellt war.
    "betriebsart": ("hc", "OperatingMode", 0, None),
    "raumsoll": ("hc", "TempDesired", 0, None),
    "absenktemp": ("hc", "TempDesiredLow", 0, None),
    "heizkurve": ("hc", "HeatingCurve", 0, None),
}

# Was einen Abbruch nahelegt. Geprueft wird bei jeder Zeile, gemeldet auf der
# Konsole -- entschieden wird am Geraet, nicht vom Skript.
KEINE_STOERUNG = "-;-;-;-;-"
DRUCK_MINIMUM = 1.0
MISCHER_WARNSCHWELLE = 35.0


def _feld(werte: dict[str, dict[str, str]], spalte: str) -> str | None:
    circuit, message, index, status = COLUMNS[spalte]
    roh = werte.get(circuit, {}).get(message)
    if index is None:
        return roh
    return ebusd.parse_field(roh, index=index, status_index=status)


def _notizen_lauschen(ablage: list[str]) -> None:
    """Getippte Zeilen einsammeln, ohne die Messschleife anzuhalten.

    Am Kessel stehend ist das der bequemste Weg, eine Phase zu markieren:
    tippen, Enter, weiter -- die Notiz erscheint in der naechsten Zeile.

    Bewusst ueber add_reader und nicht ueber einen Thread: ein blockierendes
    readline im Executor haengt beim Beenden so lange, bis noch einmal Enter
    gedrueckt wird. Ist stdin nicht lesbar (Dienst, Umleitung), laeuft die
    Messung eben ohne Notizen weiter.
    """
    def abmelden() -> None:
        with contextlib.suppress(OSError, ValueError, NotImplementedError):
            asyncio.get_running_loop().remove_reader(sys.stdin.fileno())

    def lesen() -> None:
        try:
            roh = os.read(sys.stdin.fileno(), 4096)
        except OSError:
            abmelden()
            return
        if not roh:
            # Ende der Eingabe. Ohne Abmelden meldet der Deskriptor das
            # unablaessig weiter -- im Hintergrundbetrieb eine Dauerschleife.
            abmelden()
            return
        for zeile in roh.decode("utf-8", errors="replace").splitlines():
            if zeile.strip():
                ablage.append(zeile.strip())

    # Ohne Terminal gibt es nichts zu tippen; dann bleibt der Deskriptor in Ruhe.
    if not sys.stdin.isatty():
        return
    with contextlib.suppress(OSError, ValueError, NotImplementedError):
        asyncio.get_running_loop().add_reader(sys.stdin.fileno(), lesen)


async def _zaehler(client: ebusd.EbusdClient) -> dict[str, str]:
    stand: dict[str, str] = {}
    for circuit, message in COUNTERS:
        try:
            wert = await client.read(circuit, message, REFRESH_MAXAGE)
        except ebusd.EbusdError as err:
            wert = f"nicht lesbar: {err}"
        stand[f"{circuit} {message}"] = wert or "-"
    return stand


async def _abtastung(client: ebusd.EbusdClient) -> dict[str, dict[str, str]]:
    werte: dict[str, dict[str, str]] = {}
    for circuit in CIRCUITS:
        try:
            werte[circuit] = await client.find(circuit)
        except ebusd.EbusdError as err:
            print(f"  {circuit}: {err}", file=sys.stderr)
            werte[circuit] = {}
    for circuit, message in REFRESH:
        try:
            wert = await client.read(circuit, message, REFRESH_MAXAGE)
        except ebusd.EbusdError as err:
            print(f"  {circuit} {message}: {err}", file=sys.stderr)
            continue
        if wert is not None:
            werte.setdefault(circuit, {})[message] = wert
    return werte


def _warnungen(zeile: dict[str, str | None]) -> list[str]:
    warnungen: list[str] = []
    stoerung = zeile.get("stoerung")
    if stoerung and stoerung != KEINE_STOERUNG:
        warnungen.append(f"Stoerung am Kessel: {stoerung}")
    druck = zeile.get("wasserdruck")
    if druck is not None:
        with contextlib.suppress(ValueError):
            if float(druck) < DRUCK_MINIMUM:
                warnungen.append(f"Wasserdruck {druck} bar")
    mischer = zeile.get("mischer_vorlauf")
    if mischer is not None:
        with contextlib.suppress(ValueError):
            if float(mischer) > MISCHER_WARNSCHWELLE:
                warnungen.append(
                    f"Mischervorlauf {mischer} °C -- bekommt die Fussbodenheizung Vorlauf?"
                )
    return warnungen


async def main() -> int:
    p = argparse.ArgumentParser(description="Messreihe für den thermischen Nachweis")
    p.add_argument("--host", default="homeassistant", help="ebusd-Host (nicht der Adapter)")
    p.add_argument("--port", type=int, default=8888, help="Kommandoport von ebusd")
    p.add_argument("--interval", type=float, default=30.0, help="Sekunden je Zeile")
    p.add_argument("--out", default="heizversuch.csv", help="Zieldatei")
    args = p.parse_args()

    client = ebusd.EbusdClient(args.host, args.port)
    ziel = pathlib.Path(args.out)

    try:
        version = await client.version()
    except ebusd.EbusdError as err:
        print(f"Keine Verbindung zu {args.host}:{args.port} -- {err}", file=sys.stderr)
        return 1

    start = dt.datetime.now().astimezone()
    anfangsstand = await _zaehler(client)

    notizen: list[str] = []
    _notizen_lauschen(notizen)

    # Ctrl-C beendet die Schleife, statt sie mitten im Abruf abzubrechen --
    # nur so lassen sich die Zaehlerstaende am Ende noch holen.
    schluss = asyncio.Event()
    with contextlib.suppress(NotImplementedError):
        asyncio.get_running_loop().add_signal_handler(signal.SIGINT, schluss.set)

    print(f"{version} auf {args.host}:{args.port}")
    print(f"Schreibt nach {ziel} -- alle {args.interval:.0f} s eine Zeile.")
    print("Text tippen und Enter druecken setzt eine Notiz in die naechste Zeile.")
    print("Beenden mit Ctrl-C.\n")

    with ziel.open("w", newline="", encoding="utf-8") as datei:
        datei.write(f"# auroMATIC Heizversuch, Start {start.isoformat(timespec='seconds')}\n")
        datei.write(f"# ebusd {version} auf {args.host}:{args.port}\n")
        for name, wert in anfangsstand.items():
            datei.write(f"# Zaehler zu Beginn: {name} = {wert}\n")

        schreiber = csv.DictWriter(
            datei, fieldnames=["zeit", "sekunden", "notiz", *COLUMNS], restval=""
        )
        schreiber.writeheader()

        zeilen = 0
        try:
            while not schluss.is_set():
                runde = asyncio.get_running_loop().time()
                jetzt = dt.datetime.now().astimezone()
                werte = await _abtastung(client)

                zeile: dict[str, str | None] = {
                    spalte: _feld(werte, spalte) for spalte in COLUMNS
                }
                notiz = " | ".join(notizen)
                notizen.clear()

                schreiber.writerow({
                    "zeit": jetzt.isoformat(timespec="seconds"),
                    "sekunden": f"{(jetzt - start).total_seconds():.0f}",
                    "notiz": notiz,
                    **zeile,
                })
                datei.flush()
                zeilen += 1

                # Die Kette in einer Zeile: gerechnet -> angefordert ->
                # geliefert -> gemessen -> zurueck.
                print(
                    f"{jetzt:%H:%M:%S}  AT {zeile['aussentemp'] or '--':>5}"
                    f"  soll {zeile['vorlauf_soll_hc'] or '--':>5}"
                    f" -> {zeile['vorlauf_soll_bai'] or '--':>5}"
                    f"  Kessel {zeile['kessel_vorlauf'] or '--':>5}"
                    f"/{zeile['kessel_ruecklauf'] or '--':>5}"
                    f"  VF1 {zeile['sammelvorlauf'] or '--':>5}"
                    f"  RL {zeile['sammelruecklauf'] or '--':>5}"
                    f"  Flamme {zeile['flamme'] or '--':>3}"
                    f"  Pumpe {zeile['kesselpumpe'] or '--':>7}"
                    f"  Sperre {zeile['heizsperre'] or '--'}"
                    + (f"  [{notiz}]" if notiz else ""),
                    # Ohne Terminal puffert Python blockweise; im
                    # Hintergrundbetrieb erschiene sonst stundenlang nichts.
                    flush=True,
                )
                for warnung in _warnungen(zeile):
                    print(f"  !! {warnung}", file=sys.stderr)

                rest = args.interval - (asyncio.get_running_loop().time() - runde)
                if rest > 0:
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(schluss.wait(), rest)
        except KeyboardInterrupt:
            pass
        finally:
            with contextlib.suppress(OSError, ValueError, NotImplementedError):
                asyncio.get_running_loop().remove_reader(sys.stdin.fileno())
            endstand = await _zaehler(client)
            for name, wert in endstand.items():
                datei.write(f"# Zaehler am Ende: {name} = {wert}\n")
            await client.close()

    dauer = dt.datetime.now().astimezone() - start
    print(f"\n{zeilen} Zeilen in {dauer} nach {ziel} geschrieben.")
    print("Zaehlerstaende, Anfang -> Ende:")
    for name, wert in anfangsstand.items():
        print(f"  {name}: {wert} -> {endstand.get(name, '-')}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(130)
