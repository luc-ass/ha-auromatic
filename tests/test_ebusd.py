#!/usr/bin/env python3
"""Pruefungen fuer die ebusd-Anbindung -- laeuft ohne Home Assistant.

Gespielt wird gegen einen Fake-ebusd, der wortgetreue Antworten der echten
Anlage zurueckliefert (ebusctl find -a vom 2026-09-01). Damit sind genau die
drei Fallstricke abgedeckt, an denen der erste MQTT-Anlauf gescheitert ist:
doppelte Nachrichtennamen, nicht angeschlossene Fuehler und Dekodierfehler
nicht vorhandener Kaskadenkessel.

    python3 tests/test_ebusd.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys

_MODULE = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "auromatic" / "ebusd.py"
_spec = importlib.util.spec_from_file_location("auromatic_ebusd", _MODULE)
ebusd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ebusd)

# Wortgetreue Auszuege aus der echten Anlage.
RESPONSES: dict[str, list[str]] = {
    "hc": [
        "hc FlowTempMax = 50",
        "hc FlowTempMax = no data stored",
        "hc FlowTempMin = 15",
        "hc OutsideTemp = 20.00;ok",
        "hc SumFlowSensor = 23.62;ok",
        "hc OperatingMode = off",
        "hc OperatingMode = no data stored",
        "hc TempDesired = 25.0",
        "hc HeatingCurve = 1.00",
        "hc Currenterror = -;-;-;-;-",
        "hc StatOperatingHours = -",
        "hc HcMaxPreHeating = no data stored (message not available due to condition)",
    ],
    "mc": [
        "mc FlowTemp = 24.62;ok",
        "mc FlowTempMax = 40",
        "mc OperatingMode = off",
        "mc Mode = 22;off;0;0;low;mixer;day",
        "mc RoomTempOffset = 0.00",
    ],
    "hwc": [
        "hwc Storage1Sensor2 = 64.88;ok",
        "hwc TempDesired2 = 50.0",
        "hwc OperatingMode2 = auto",
    ],
    "sc": [
        "sc Coll1Sensor = 68.69;ok",
        "sc Coll2Sensor = -19.38;cutoff",
        "sc Storage1Sensor3 = 64.69;ok",
        "sc Storage3Sensor3 = -13.94;cutoff",
        "sc CollPumpHRuntime1 = 8275",
        "sc SolCollPumpED1 = 0",
    ],
    "ui": [
        "ui FlowTempDesiredB1 = 0",
        "ui FlowTempDesiredB2 =  (ERR: invalid position for 3115b509030d4810 / 00)",
        "ui RoomTemp = 30.00;ok",
        "ui StateEM = no data stored",
        "ui SystemModeStream1 = heat",
        "ui YieldThisYear = 26;38;157;0;0;0;138;244;0;0;0;0",
        "ui BoilerHoursB1 = 60836",
    ],
    "cc": ["cc StatPowerOn = 204"],
}

writes: list[str] = []
checks = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not condition:
        raise AssertionError(f"{label}: {detail or 'fehlgeschlagen'}")
    print(f"  {label:.<30} {detail}")


async def _serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while (raw := await reader.readline()):
        cmd = raw.decode().strip()
        if cmd == "info":
            out = ["version: ebusd 26.1.26.1", "signal: acquired", "masters: 4"]
        elif cmd.startswith("find -c "):
            out = RESPONSES.get(cmd.split()[-1], [])
        elif cmd.startswith("write "):
            writes.append(cmd)
            out = ["done"]
        else:
            out = ["ERR: command not found"]
        writer.write(("\n".join(out) + "\n\n").encode())
        await writer.drain()
    writer.close()


async def run() -> None:
    server = await asyncio.start_server(_serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = ebusd.EbusdClient("127.0.0.1", port)
    pf = ebusd.parse_field

    version = await client.version()
    check("Versionsabfrage", version == "ebusd 26.1.26.1", version)

    hc = await client.find("hc")
    check("Doppelte Namen", hc.get("FlowTempMax") == "50",
          "Lesewert gewinnt gegen leere Schreibvariante")
    check("Bedingte Nachrichten", "HcMaxPreHeating" not in hc, "gefiltert")
    check("Enum-Werte", hc.get("OperatingMode") == "off", "off")

    ui = await client.find("ui")
    check("Kaskaden-Dekodierfehler", "FlowTempDesiredB2" not in ui,
          "B2-B8 verworfen, B1 bleibt")
    check("Ertragsstatistik", ui["YieldThisYear"].count(";") == 11, "12 Monatswerte")

    sc = await client.find("sc")
    check("Fuehler ohne Anschluss", pf(sc["Coll2Sensor"], status_index=1) is None,
          "cutoff wird zu None statt -19.38 Grad")
    check("Fuehler mit Anschluss", pf(sc["Storage1Sensor3"], status_index=1) == "64.69", "64.69")
    check("Leerer Zaehler", pf(hc["StatOperatingHours"]) is None, "'-' wird abgewiesen")
    check("Feldauswahl", pf(hc["OutsideTemp"], status_index=1) == "20.00", "20.00 aus '20.00;ok'")

    mc = await client.find("mc")
    check("Sammelnachricht Mode", pf(mc["Mode"], index=1) == "off",
          "Betriebsart aus Feld 1 lesbar")
    check("Estrichtrocknung", pf(mc["Mode"], index=2) == "0" and pf(mc["Mode"], index=3) == "0",
          "Tage und Temperatur stehen auf 0")

    # Der Fehlerspeicher wird bewusst als Gesamtwert bewertet.
    def has_error(raw: str) -> bool:
        return any(part.strip() not in ("", "-") for part in raw.split(";"))

    check("Stoerungsfrei", has_error(hc["Currenterror"]) is False, "'-;-;-;-;-'")
    check("Stoerung erkannt", has_error("-;-;F.22;-;-") is True, "F.22")

    await client.write("mc", "SetMode", "auto")
    check("Schreibbefehl", writes == ["write -c mc SetMode auto"],
          "Einzelfeld-Nachricht, nicht 'Mode'")

    try:
        await client.command("bogus")
    except ebusd.EbusdCommandError as err:
        check("Fehlerantwort", True, f"als Ausnahme: {err}")
    else:
        raise AssertionError("ERR wurde nicht als Ausnahme gemeldet")

    # Grundlage der Adress-Suche: eine tote Adresse muss schnell und sauber
    # scheitern, sonst blockiert das Anklopfen den Einrichtungsdialog.
    dead = ebusd.EbusdClient("127.0.0.1", 1, timeout=2.0)
    try:
        await dead.version()
    except ebusd.EbusdError as err:
        check("Tote Adresse", "fehlgeschlagen" in str(err), "scheitert sauber statt zu haengen")
    else:
        raise AssertionError("geschlossener Port meldete keinen Fehler")
    finally:
        await dead.close()

    await client.close()
    server.close()


def main() -> int:
    print("=== auroMATIC: ebusd-Anbindung ===")
    try:
        asyncio.run(asyncio.wait_for(run(), 20))
    except AssertionError as err:
        print(f"\nFEHLGESCHLAGEN: {err}")
        return 1
    print(f"\n{checks} Pruefungen bestanden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
