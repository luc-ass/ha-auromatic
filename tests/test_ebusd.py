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

# Der Regler uebernimmt einen geschriebenen Wert sofort, der Cache von ebusd
# nicht: 'find' liefert bis zum naechsten Poll weiter den alten Stand. Genau
# daran ist die Betriebsart in der Oberflaeche zurueckgesprungen. Der Fake
# bildet das nach -- 'find' bleibt stur, 'read -f' geht an den Regler.
# Die Register sind in der ebusd-Konfiguration als "r;w" deklariert: ein
# Schreibvorgang auf 'OperatingMode' aendert genau den Wert, den 'OperatingMode'
# auch liest. Die Set*-Nachrichten aus mcmode_inc.tsp sind nicht der Weg -- es
# gibt sie im Heizkreis gar nicht.
WRITABLE = frozenset({
    "OperatingMode", "TempDesired", "TempDesiredLow", "HeatingCurve",
    "OperatingMode2", "TempDesired2",
    "SolEnableDiffTemp1", "SolDisableDiffTemp1",
})
device: dict[str, str] = {}

writes: list[str] = []
polls: list[str] = []
restarted: list[int] = []
scan_state: list[str] = ["finished"]
poll_list: set[str] = set()
commands: list[str] = []
checks = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not condition:
        raise AssertionError(f"{label}: {detail or 'fehlgeschlagen'}")
    print(f"  {label:.<30} {detail}")


# Leseanfragen mit Höchstalter: die Register außerhalb der Poll-Warteschlange.
cached_reads: list[str] = []


async def _serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while (raw := await reader.readline()):
        cmd = raw.decode().strip()
        commands.append(cmd)
        if cmd == "info":
            out = ["version: ebusd 26.1.26.1", "signal: acquired",
                   f"scan: {scan_state[0]}", "masters: 4",
                   f"poll: {len(poll_list)}", "update: 10"]
        elif cmd.startswith("find -c "):
            out = RESPONSES.get(cmd.split()[-1], [])
        elif cmd.startswith("write "):
            writes.append(cmd)
            # write -c <circuit> <SetXxx> <wert>
            _, _, circuit, message, value = cmd.split(maxsplit=4)
            if message in WRITABLE:
                device[f"{circuit} {message}"] = value
            out = ["done"]
        elif cmd.startswith("read -p "):
            # read -p <prio> -m <maxage> -c <circuit> <nachricht>
            parts = cmd.split()
            polls.append(cmd)
            circuit, message = parts[6], parts[7]
            value = next(
                (line.partition(" = ")[2] for line in RESPONSES.get(circuit, [])
                 if line.startswith(f"{circuit} {message} = ")),
                None,
            )
            # Nur was ebusd kennt, landet in der Poll-Liste.
            if value is not None:
                poll_list.add(f"{circuit} {message}")
            out = [value] if value is not None else ["ERR: element not found"]
        elif cmd.startswith("read -m ") and " -c " in cmd:
            # read -m <hoechstalter> -c <circuit> <nachricht>: ebusd antwortet
            # aus dem Zwischenspeicher, solange der Wert jung genug ist. Der
            # Fake hat immer einen -- genau der Fall, der keinen Bus kostet.
            parts = cmd.split()
            circuit, message = parts[4], parts[5]
            cached_reads.append(cmd)
            value = next(
                (line.partition(" = ")[2] for line in RESPONSES.get(circuit, [])
                 if line.startswith(f"{circuit} {message} = ")),
                None,
            )
            out = [value] if value is not None else ["ERR: element not found"]
        elif cmd.startswith("read -f -c "):
            # read -f -c <circuit> <nachricht>
            circuit, message = cmd.split()[3], cmd.split()[4]
            cached = next(
                (line.partition(" = ")[2] for line in RESPONSES.get(circuit, [])
                 if line.startswith(f"{circuit} {message} = ")),
                None,
            )
            value = device.get(f"{circuit} {message}", cached)
            out = [value] if value is not None else ["ERR: element not found"]
        elif cmd == "neustart" and not restarted:
            # Bildet einen Neustart von ebusd nach: die Verbindung faellt weg,
            # und mit ihr die Poll-Liste im Speicher von ebusd. Nur beim ersten
            # Mal -- der Wiederholungsversuch des Clients soll durchkommen.
            restarted.append(1)
            writer.close()
            return
        elif cmd == "neustart":
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
    # Die Jahressumme braucht alle Felder. parse_field liefert nur das erste --
    # damit stand der Januar als Jahresertrag in der Oberflaeche.
    check("Jahressumme", ebusd.sum_fields(ui["YieldThisYear"]) == 603,
          "603 kWh aus zwoelf Monaten, nicht 26 (Januar)")
    check("Summe ohne Zahlen", ebusd.sum_fields("-;-") is None, "None statt Ausnahme")

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

    await client.write("mc", "OperatingMode", "auto")
    check("Schreibbefehl", writes == ["write -c mc OperatingMode auto"],
          "Einzelfeld-Register, nicht die Sammelnachricht 'Mode'")

    # Der Fehler, um den es geht: nach dem Schreiben liefert der Cache
    # unveraendert "off". Wer sich darauf verlaesst, stellt die Betriebsart in
    # der Oberflaeche sofort wieder zurueck.
    check("Cache bleibt alt", (await client.find("mc"))["OperatingMode"] == "off",
          "'find' kennt den neuen Wert noch nicht")

    del commands[:]
    confirmed = await client.write_and_confirm("mc", "OperatingMode", "eco", "OperatingMode")
    check("Nachlesen nach Schreiben", confirmed == "eco", "'eco' statt Cache-Wert 'off'")
    check("Cache umgangen",
          commands == ["write -c mc OperatingMode eco", "read -f -c mc OperatingMode"],
          "genau ein zusaetzlicher Roundtrip, nur nach Benutzeraktion")

    # Scheitert das Nachlesen, bleibt es beim quittierten Schreibvorgang --
    # ein Lesefehler darf die Bedienung nicht als Fehlschlag aussehen lassen.
    check("Nachlesen scheitert",
          await client.write_and_confirm("hwc", "OperatingMode2", "auto", "GibtsNicht") is None,
          "None statt Ausnahme")

    # Die Poll-Liste von ebusd steht in keiner CSV -- sie ist Laufzeitzustand.
    # Ohne diese Anmeldung holt ebusd das Register nie wieder vom Bus, und
    # 'find' liefert stumm den letzten bekannten Wert.
    del polls[:]
    await client.set_poll_priority("sc", "Coll1Sensor", 1, 3600)
    check("Poll-Anmeldung", polls == ["read -p 1 -m 3600 -c sc Coll1Sensor"],
          "Prioritaet und Hoechstalter am read-Kommando")
    check("Anmeldung ohne Buszugriff", "-f" not in polls[0],
          "'-m' laesst den Zwischenspeicher antworten")

    # Waehrend ebusd scannt, sind die Definitionen unvollstaendig -- eine
    # Anmeldung in diesem Fenster scheitert fuer alles, was noch nicht dran
    # war. Genau daran ist der erste Anlauf gescheitert.
    check("Scan fertig erkannt", (await client.status())[0] is True, "scan: finished")
    scan_state[0] = "running"
    check("Scan laeuft erkannt", (await client.status())[0] is False, "scan: running")
    scan_state[0] = "finished"

    # Ein Register, das diese Anlage nicht kennt, darf die uebrigen vierzig
    # nicht mitreissen -- der Koordinator faengt den Fehler je Register ab.
    try:
        await client.set_poll_priority("sc", "GibtsNicht", 1, 3600)
        check("Unbekanntes Register", False, "haette scheitern muessen")
    except ebusd.EbusdCommandError as err:
        check("Unbekanntes Register", True, f"als Ausnahme: {err}")

    # Die Groesse der Poll-Liste ist das Signal, an dem der Koordinator merkt,
    # dass ein Rescan von ebusd die Anmeldung herausgeworfen hat.
    check("Poll-Liste gemeldet", (await client.status())[1] == len(poll_list),
          f"{len(poll_list)} Eintraege")
    poll_list.clear()
    check("Leere Poll-Liste erkannt", (await client.status())[1] == 0,
          "nach einem Rescan faellt sie auf 0")

    # Zwei Register stehen nicht in der Warteschlange, sondern werden vom
    # Koordinator selbst geholt -- mit Höchstalter statt mit '-f'. Die
    # Ertragsstatistik ist zwölf Felder breit, jedes Feld ein eigenes
    # Telegramm; in der Warteschlange fraß sie ein Fünftel aller Anfragen für
    # zwei Werte, die sich einmal am Tag ändern.
    value = await client.read("ui", "YieldThisYear", 3600)
    check("Lesen mit Höchstalter", value is not None and value.count(";") == 11,
          "zwölf Monatswerte")
    check("Kein Buszugriff erzwungen", "-f" not in cached_reads[-1],
          f"'{cached_reads[-1]}'")

    # Eine mehrfeldrige Nachricht hat waehrend ihres eigenen Lesevorgangs
    # keinen Wert: 'find' liefert sie dann gar nicht, und die Entitaet fiel
    # fuer einen Zyklus auf 'unavailable' -- sechsmal in 16 Stunden an der
    # laufenden Anlage. Die Ueberbrueckung faengt das ab, aber nur befristet.
    vorher = {"ui": {"YieldThisYear": "26;38;157", "RoomTemp": "30.06;ok"}}
    jetzt = {"ui": {"RoomTemp": "30.12;ok"}}
    offen, abgelaufen = ebusd.carry_forward(vorher, jetzt, {}, 1000.0, 600)
    check("Lücke überbrückt", jetzt["ui"]["YieldThisYear"] == "26;38;157",
          "letzter Wert gilt weiter")
    check("Nur die Lücke", jetzt["ui"]["RoomTemp"] == "30.12;ok",
          "vorhandene Werte bleiben unangetastet")
    check("Ausfall vorgemerkt", list(offen) == [("ui", "YieldThisYear")] and not abgelaufen,
          "seit 1000.0")

    spaeter = {"ui": {"RoomTemp": "30.12;ok"}}
    offen2, abgelaufen2 = ebusd.carry_forward(vorher, spaeter, offen, 1601.0, 600)
    check("Frist läuft ab", abgelaufen2 == [("ui", "YieldThisYear")] and not offen2,
          "nach 601 s gilt der Wert als weg")
    check("Wert nicht mehr geliefert", "YieldThisYear" not in spaeter["ui"],
          "die Entität darf jetzt 'unavailable' werden")

    zurueck = {"ui": {"YieldThisYear": "26;38;158", "RoomTemp": "30.12;ok"}}
    offen3, abgelaufen3 = ebusd.carry_forward(vorher, zurueck, offen, 1200.0, 600)
    check("Rückkehr beendet die Überbrückung", not offen3 and not abgelaufen3,
          "der Merker verschwindet mit dem Wert")

    # Ein Neustart von ebusd reisst die Verbindung ab. Der naechste Befehl muss
    # trotzdem durchkommen -- sonst faellt die ganze Integration aus, nur weil
    # das Add-on neu gestartet wurde.
    await client.command("neustart")
    check("Neustart ueberbrueckt", (await client.find("hc"))["OperatingMode"] == "off",
          "Befehl nach Abriss gelingt im zweiten Versuch")
    check("Abriss war echt", restarted == [1], "Fake hat die Verbindung einmal fallen lassen")

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
