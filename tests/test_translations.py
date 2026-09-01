#!/usr/bin/env python3
"""Prüft die Entitäten gegen die Home-Assistant-Richtlinien -- ohne HA.

Namen und Icons stehen nicht mehr im Code, sondern in translations/ und
icons.json. Fehlt dort ein Eintrag, bleibt die Entität in der Oberfläche
namenlos, ohne dass irgendetwas fehlschlägt. Genau das fängt diese Datei ab.

Ebenso still scheitert ein falscher Schreibname: ebusd antwortet mit
"ERR: element not found", und zwar erst, wenn jemand den Wert verstellt.
Deshalb wird hier auch geprüft, dass jede Schreibnachricht so heißt wie die
Lesenachricht -- die Register sind in der ebusd-Konfiguration als "r;w"
deklariert.

    python3 tests/test_translations.py
"""

from __future__ import annotations

import ast
import importlib.util
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "auromatic"

_spec = importlib.util.spec_from_file_location("auromatic_const", ROOT / "const.py")
const = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(const)

_poll_spec = importlib.util.spec_from_file_location("auromatic_poll", ROOT / "poll.py")
poll_module = importlib.util.module_from_spec(_poll_spec)
_poll_spec.loader.exec_module(poll_module)
poll_set = poll_module.POLL_SET
poll_passive = poll_module.POLL_PASSIVE

# Plattform -> Modul. water_heater fehlt bewusst: seine Entität ist das
# Hauptmerkmal des Geräts und trägt deshalb keinen eigenen Namen.
PLATFORMS = ("sensor", "binary_sensor", "select", "number")

checks = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not condition:
        raise AssertionError(f"{label}: {detail or 'fehlgeschlagen'}")
    print(f"  {label:.<34} {detail}")


def descriptions(platform: str) -> dict[str, dict]:
    """Alle Entitätsbeschreibungen einer Plattform statisch einsammeln."""
    tree = ast.parse((ROOT / f"{platform}.py").read_text())
    found: dict[str, dict] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", "")
        if not name.endswith("Description") or not node.keywords:
            continue
        fields = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        key = fields.get("key")
        if not isinstance(key, ast.Constant):
            continue
        options: list[str] = []
        if isinstance(fields.get("options"), ast.List):
            for element in fields["options"].elts:
                if isinstance(element, ast.Constant):
                    options.append(element.value)
                elif isinstance(element, ast.Starred) and getattr(element.value, "id", "") == "MODE_OPTIONS":
                    options.extend(const.MODE_OPTIONS)
        # ENUM zaehlt nicht: diese device_class beschreibt nur die moeglichen
        # Zustaende und liefert kein Symbol, ein eigenes Icon ist dort erlaubt.
        device_class = ast.unparse(fields["device_class"]) if "device_class" in fields else ""
        found[key.value] = {
            "device_class": (
                (bool(device_class) and not device_class.endswith("ENUM"))
                or "**_TEMP" in ast.unparse(node)
            ),
            "options": options,
            "icon": "icon" in fields,
            "name": "name" in fields,
        }
    return found


def write_paths(platform: str) -> list[tuple[str, str, str, str]]:
    """(Kreis, Schluessel, Lesename, Schreibname) aller schreibenden Entitäten.

    Bewusst getrennt von descriptions(): dort ist der Uebersetzungsschluessel
    der Schluessel, und Heizkreis und Mischerkreis teilen sich denselben --
    ein Fehler in genau einem der beiden Kreise faellt dabei unter den Tisch.
    """
    tree = ast.parse((ROOT / f"{platform}.py").read_text())
    paths: list[tuple[str, str, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not getattr(node.func, "id", "").endswith("Description"):
            continue
        fields = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        if "write_message" not in fields:
            continue
        values = {
            name: fields[name].value
            for name in ("circuit", "key", "message", "write_message")
            if isinstance(fields.get(name), ast.Constant)
        }
        if len(values) == 4:
            paths.append(
                (values["circuit"], values["key"], values["message"], values["write_message"])
            )
    return paths


def used_messages() -> set[tuple[str, str]]:
    """Alle (Kreis, Nachricht), die die Integration tatsächlich ausliest.

    Zwei Quellen: die Entitätsbeschreibungen und die Handvoll Namen, die als
    Literale in coordinator.value(...) stehen -- der Warmwasserspeicher hat
    keine Beschreibungsklasse für seine Soll- und Betriebsartregister.
    """
    found: set[tuple[str, str]] = set()
    for platform in (*PLATFORMS, "water_heater"):
        tree = ast.parse((ROOT / f"{platform}.py").read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "id", "").endswith("Description"):
                fields = {kw.arg: kw.value for kw in node.keywords if kw.arg}
                circuit, message = fields.get("circuit"), fields.get("message")
                if isinstance(circuit, ast.Constant) and isinstance(message, ast.Constant):
                    found.add((circuit.value, message.value))
            elif getattr(node.func, "attr", "") == "value" and len(node.args) >= 2:
                circuit, message = node.args[0], node.args[1]
                if isinstance(circuit, ast.Constant) and isinstance(message, ast.Constant):
                    found.add((circuit.value, message.value))
    return found


def main() -> int:
    print("=== auroMATIC: Entitäten gegen die HA-Richtlinien ===")
    try:
        entities = {platform: descriptions(platform) for platform in PLATFORMS}
        translations = {
            lang: json.loads((ROOT / "translations" / f"{lang}.json").read_text())["entity"]
            for lang in ("en", "de")
        }
        strings = json.loads((ROOT / "strings.json").read_text())["entity"]
        icons = json.loads((ROOT / "icons.json").read_text())["entity"]

        total = sum(len(found) for found in entities.values())
        check("Beschreibungen gefunden", total > 25, f"{total} Entitäten in {len(PLATFORMS)} Plattformen")

        for platform, found in entities.items():
            for key, fields in found.items():
                check(f"{platform}.{key}: kein Name im Code", not fields["name"],
                      "Name kommt aus translations/")
                check(f"{platform}.{key}: kein Icon im Code", not fields["icon"],
                      "Icon kommt aus icons.json")
                for lang, tree in translations.items():
                    entry = tree.get(platform, {}).get(key, {})
                    check(f"{platform}.{key}: Name in {lang}", bool(entry.get("name")),
                          entry.get("name", ""))
                for option in fields["options"]:
                    for lang, tree in translations.items():
                        states = tree.get(platform, {}).get(key, {}).get("state", {})
                        check(f"{platform}.{key}.{option} in {lang}", option in states,
                              states.get(option, ""))

        # Steht ein Register nicht im Poll-Satz, holt ebusd es nicht mehr vom
        # Bus. find liefert dann bis in alle Ewigkeit den letzten bekannten
        # Wert -- ohne Fehlermeldung und ohne "unavailable". Deshalb muss der
        # Satz genau dem entsprechen, was die Plattformen auslesen.
        poll = {(circuit, message) for circuit, msgs in poll_set.items() for message in msgs}
        used = used_messages()
        check("Poll-Satz gefunden", len(poll) > 30, f"{len(poll)} Register")
        # Ein Register darf nur dann fehlen, wenn es ausdruecklich als passiv
        # vermerkt ist -- schreibende Nachrichten wie mc RoomTempOffset lassen
        # sich nicht pollen, ebusd hoert sie nur mit.
        check("passive Register vermerkt", poll.isdisjoint(poll_passive),
              f"{len(poll_passive)} nicht pollbar")
        for circuit, message in sorted(used - poll - poll_passive):
            check(f"{circuit}.{message}: im Poll-Satz", False, "wird gelesen, aber nicht angemeldet")
        check("kein Register ohne Anmeldung", not (used - poll - poll_passive),
              f"{len(used)} gelesen")
        for circuit, message in sorted(poll_passive - used):
            check(f"{circuit}.{message}: passiv, aber ungenutzt", False, "niemand liest es")
        for circuit, message in sorted(poll - used):
            check(f"{circuit}.{message}: wird gebraucht", False, "angemeldet, aber niemand liest es")
        check("kein Register auf Vorrat", not (poll - used), "Satz deckt sich mit dem Bedarf")

        # Ein Schreibname, den es im Kreis nicht gibt, faellt erst beim
        # Verstellen auf. Beide Namen muessen uebereinstimmen: die Register
        # sind "r;w", die Set*-Nachrichten gibt es nur im Mischerkreis.
        written = 0
        for platform in PLATFORMS:
            for circuit, key, message, write_message in write_paths(platform):
                written += 1
                check(f"{platform}.{circuit}.{key}: Schreibname = Lesename",
                      write_message == message, message)
        check("schreibende Entitäten gefunden", written >= 8, f"{written} Schreibwege")

        # water_heater hat keine Beschreibungsklasse -- seine beiden
        # Schreibaufrufe stehen als Literale im Code.
        wh = ast.parse((ROOT / "water_heater.py").read_text())
        for node in ast.walk(wh):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "attr", "") != "_write" or len(node.args) != 3:
                continue
            write_message, _, read_message = node.args
            if not isinstance(write_message, ast.Constant):
                continue
            check(f"water_heater.{read_message.value}: Schreibname = Lesename",
                  write_message.value == read_message.value, read_message.value)

        # Der Select bietet die Betriebsarten aus const an, nicht aus einer
        # Beschreibung -- sie brauchen trotzdem übersetzte Bezeichnungen.
        for option in const.MODE_OPTIONS:
            for lang, tree in translations.items():
                states = tree["select"]["mode"]["state"]
                check(f"select.mode.{option} in {lang}", option in states, states.get(option, ""))

        for platform, entries in icons.items():
            for key in entries:
                fields = entities[platform].get(key)
                check(f"icons.json: {platform}.{key} existiert", fields is not None)
                # Wo eine device_class ein Symbol liefert, ist ein eigenes Icon
                # ausdrücklich unerwünscht.
                check(f"icons.json: {platform}.{key} ohne device_class",
                      not fields["device_class"], "Standardsymbol wird nicht überschrieben")

        # Der Speicher traegt den Geraetenamen, hat also keinen eigenen -- aber
        # seine Betriebsarten erscheinen als Auswahl und muessen uebersetzt sein.
        for option in const.MODE_OPTIONS:
            for lang, tree in translations.items():
                states = tree["water_heater"]["hot_water"]["state_attributes"]["operation_mode"]["state"]
                check(f"water_heater.{option} in {lang}", option in states, states.get(option, ""))

        for platform, tree in translations["de"].items():
            if platform == "water_heater":
                continue
            for key in tree:
                check(f"de.json: {platform}.{key} wird benutzt", key in entities[platform],
                      "keine verwaiste Übersetzung")

        check("strings.json deckt sich mit en.json", strings == translations["en"],
              "englische Quelle und Übersetzung identisch")

        for platform in (*PLATFORMS, "water_heater"):
            source = (ROOT / f"{platform}.py").read_text()
            check(f"{platform}: PARALLEL_UPDATES gesetzt", "PARALLEL_UPDATES" in source)
    except AssertionError as err:
        print(f"\nFEHLGESCHLAGEN: {err}")
        return 1
    except KeyError as err:
        print(f"\nFEHLGESCHLAGEN: unbekannter Schlüssel {err}")
        return 1
    print(f"\n{checks} Prüfungen bestanden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
