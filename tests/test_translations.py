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
poll_exempt = set(poll_module.POLL_EXEMPT)
read_maxage = set(poll_module.READ_MAXAGE)

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
        # Home Assistant beschriftet nach `translation_key`, wo einer gesetzt
        # ist, und erst sonst nach `key`. Beides faellt auseinander, wo
        # derselbe Rohwert je Kreis anders heisst -- `key` steckt in der
        # `unique_id` und darf sich dabei nicht aendern.
        if isinstance(fields.get("translation_key"), ast.Constant):
            key = fields["translation_key"]
        options: list[str] = []
        if isinstance(fields.get("options"), ast.List):
            for element in fields["options"].elts:
                if isinstance(element, ast.Constant):
                    options.append(element.value)
                elif isinstance(element, ast.Starred):
                    # `options=[*MODE_OPTIONS, "disabled"]` und Verwandte: die
                    # Liste steht in const.py, der Name hier.
                    options.extend(getattr(const, getattr(element.value, "id", ""), []))
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
                # Gelesen wird aus `source_circuit`, wo es gesetzt ist: die
                # Entität hängt dann an einem anderen Gerät als das Register.
                circuit = fields.get("source_circuit") or fields.get("circuit")
                if not isinstance(circuit, ast.Constant):
                    continue
                # `plus_message` ist das zweite Register eines zweigeteilten
                # Zaehlers: `bai HcStarts` fuehrt nur die Hunderter, die
                # beiden letzten Stellen stehen daneben. Es wird genauso
                # gelesen wie `message` und muss deshalb genauso angemeldet
                # sein -- sonst fiele es still aus der Warteschlange und die
                # Summe haenge fuer immer am letzten bekannten Rest.
                for name in ("message", "plus_message"):
                    wert = fields.get(name)
                    if isinstance(wert, ast.Constant):
                        found.add((circuit.value, wert.value))
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
        # Register aus READ_MAXAGE stehen nicht in der Warteschlange, bleiben
        # aber frisch: der Koordinator holt sie selbst mit 'read -m'. Für die
        # Vollständigkeitsprüfung zählen sie deshalb wie angemeldet.
        check("Nichts doppelt geholt", poll.isdisjoint(read_maxage),
              f"{len(read_maxage)} Register außerhalb der Warteschlange")
        poll |= read_maxage
        # Ein Register darf nur dann fehlen, wenn es in POLL_EXEMPT steht --
        # dort mit Grund, denn jede Ausnahme ist eine Entscheidung: schreibende
        # Nachrichten lassen sich nicht pollen, nicht angeschlossene Fuehler
        # wuerden einen Platz in der Warteschlange fuer nichts belegen.
        check("Ausnahmen nicht doppelt", poll.isdisjoint(poll_exempt),
              f"{len(poll_exempt)} ausgenommen")
        for key, grund in poll_module.POLL_EXEMPT.items():
            check(f"{key[0]}.{key[1]}: Grund vermerkt", bool(grund), grund)
        for circuit, message in sorted(used - poll - poll_exempt):
            check(f"{circuit}.{message}: im Poll-Satz", False, "wird gelesen, aber nicht angemeldet")
        check("kein Register ohne Anmeldung", not (used - poll - poll_exempt),
              f"{len(used)} gelesen")
        for circuit, message in sorted(poll_exempt - used):
            check(f"{circuit}.{message}: ausgenommen, aber ungenutzt", False, "niemand liest es")
        for circuit, message in sorted(poll - used):
            check(f"{circuit}.{message}: wird gebraucht", False, "angemeldet, aber niemand liest es")
        check("kein Register auf Vorrat", not (poll - used), "Satz deckt sich mit dem Bedarf")

        # Wer aus einem fremden Kreis liest, darf dort nicht hineinschreiben:
        # der Schreibbefehl geht immer an `circuit`, der Lesewert kaeme aus
        # `source_circuit` -- das Bedienelement zeigte dann etwas anderes an,
        # als es verstellt.
        for platform in PLATFORMS:
            tree = ast.parse((ROOT / f"{platform}.py").read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if not getattr(node.func, "id", "").endswith("Description"):
                    continue
                fields = {kw.arg for kw in node.keywords if kw.arg}
                check(f"{platform}: fremder Kreis nur lesend",
                      not ({"source_circuit", "write_message"} <= fields),
                      "source_circuit und write_message schliessen sich aus")

        # Das Register, das die Wirkung eines Schreibvorgangs zeigt, wird
        # unmittelbar danach mitgelesen. Es muss eines sein, das die
        # Integration ohnehin führt -- sonst landete ein Wert im Datenbestand,
        # den kein Abruf je erneuert, und die Anzeige fröre auf dem Stand der
        # letzten Benutzeraktion ein.
        gefuehrt = {(c, msg) for c, msgs in poll_set.items() for msg in msgs} | read_maxage
        wirkungen = 0
        for platform in PLATFORMS:
            tree = ast.parse((ROOT / f"{platform}.py").read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if not getattr(node.func, "id", "").endswith("Description"):
                    continue
                fields = {kw.arg: kw.value for kw in node.keywords if kw.arg}
                wirkung = fields.get("effect_message")
                if not isinstance(wirkung, ast.Tuple):
                    continue
                teile = tuple(e.value for e in wirkung.elts if isinstance(e, ast.Constant))
                wirkungen += 1
                key = fields["key"].value
                check(f"{platform}.{key}: Wirkungsregister wird gefuehrt",
                      len(teile) == 2 and teile in gefuehrt, " ".join(teile))
        check("Wirkungsregister gefunden", wirkungen >= 1, f"{wirkungen} Stueck")

        # Ein schreibbarer Wert landet ohne `entity_category` unter den
        # Bedienelementen des Geraets -- gleichrangig mit dem, was man
        # taeglich anfasst. Das sind an dieser Anlage genau drei Groessen: die
        # beiden Raumsollwerte und die Betriebsart. Alles andere ist Auslegung
        # und gehoert in die Konfiguration; die Kategorie muss dafuer an der
        # Beschreibung selbst stehen und nicht in einem gemeinsamen Buendel,
        # sonst sieht sie weder ein Leser noch diese Pruefung.
        ALLTAG = ("temp_desired", "temp_desired_low")
        for platform in ("number", "select"):
            tree = ast.parse((ROOT / f"{platform}.py").read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if not getattr(node.func, "id", "").endswith("Description"):
                    continue
                fields = {kw.arg: kw.value for kw in node.keywords if kw.arg}
                if "key" not in fields:
                    continue
                key = fields["key"].value
                if key in ALLTAG or platform == "select":
                    check(f"{platform}.{key}: Bedienelement des Alltags",
                          "entity_category" not in fields, "ohne Kategorie")
                    continue
                check(f"{platform}.{key}: Konfiguration, nicht Bedienelement",
                      "entity_category" in fields,
                      ast.unparse(fields.get("entity_category", ast.Constant(""))))

        # Die Gerätezuordnung darf von `circuit` abweichen -- sie ist reine
        # Darstellung, waehrend `circuit` in der `unique_id` steckt. Sie muss
        # aber auf ein Geraet zeigen, das es gibt, und etwas anderes sagen als
        # `circuit`, sonst ist sie nur Rauschen.
        geraete = 0
        for platform in PLATFORMS:
            tree = ast.parse((ROOT / f"{platform}.py").read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if not getattr(node.func, "id", "").endswith("Description"):
                    continue
                fields = {kw.arg: kw.value for kw in node.keywords if kw.arg}
                geraet = fields.get("device_circuit")
                if geraet is None:
                    continue
                name = ast.unparse(geraet)
                wert = geraet.value if isinstance(geraet, ast.Constant) else (
                    const.ROOT_DEVICE if name == "ROOT_DEVICE" else name)
                key = fields["key"].value
                geraete += 1
                check(f"{platform}.{key}: Geraet bekannt",
                      wert == const.ROOT_DEVICE or wert in const.CIRCUITS, wert)
                kreis = fields.get("circuit")
                check(f"{platform}.{key}: Geraet weicht ab",
                      not isinstance(kreis, ast.Constant) or kreis.value != wert,
                      f"{ast.unparse(kreis)} -> {wert}")
        check("Geraetezuordnungen gefunden", geraete >= 6, f"{geraete} abweichende Geraete")

        # Ein Schreibname, den es im Kreis nicht gibt, faellt erst beim
        # Verstellen auf. Beide Namen muessen uebereinstimmen: die Register
        # sind "r;w", die Set*-Nachrichten gibt es nur im Mischerkreis.
        #
        # Ausnahmen gibt es, aber nur benannte: const.WRITE_EXCEPTIONS nennt
        # jede mit Kreis, Lese- und Schreibname und verlangt einen Grund. Der
        # Zirkulationskreis steht dort, weil ebusd fuer ihn kein einfeldriges
        # Leseregister kennt -- das ist eine Luecke der Konfiguration, keine
        # des Reglers, und sie soll auffallen, wenn sie sich schliesst.
        written = 0
        used_exceptions: set[tuple[str, str, str]] = set()
        for platform in PLATFORMS:
            for circuit, key, message, write_message in write_paths(platform):
                written += 1
                exception = (circuit, message, write_message)
                if exception in const.WRITE_EXCEPTIONS:
                    used_exceptions.add(exception)
                    check(f"{platform}.{circuit}.{key}: benannte Ausnahme",
                          bool(const.WRITE_EXCEPTIONS[exception]),
                          const.WRITE_EXCEPTIONS[exception])
                    continue
                check(f"{platform}.{circuit}.{key}: Schreibname = Lesename",
                      write_message == message, message)
        check("schreibende Entitäten gefunden", written >= 8, f"{written} Schreibwege")
        for exception in sorted(set(const.WRITE_EXCEPTIONS) - used_exceptions):
            check(f"{exception[0]}.{exception[1]}: Ausnahme ungenutzt", False,
                  "niemand schreibt darueber")
        check("keine Ausnahme auf Vorrat",
              not (set(const.WRITE_EXCEPTIONS) - used_exceptions),
              f"{len(const.WRITE_EXCEPTIONS)} Ausnahmen, alle benutzt")

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

        # Die Auswahl steht seit dem Zirkulationskreis in den Beschreibungen
        # (nicht jeder Kreis kennt dieselben Stufen) und wird oben schon
        # geprüft. Hier bleibt die Gegenrichtung: der Select muss alle
        # Betriebsarten anbieten koennen, die ein Heizkreis melden kann.
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
        # seine Betriebsarten erscheinen als Auswahl und muessen uebersetzt
        # sein. Es sind die des Warmwasserkreises, nicht die der Heizkreise:
        # die Anleitung fuehrt Warmwasser und Zirkulation gemeinsam mit Auto,
        # Ein und Aus (0020094390, Tab. 3.3).
        for option in const.HWC_MODE_OPTIONS:
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
