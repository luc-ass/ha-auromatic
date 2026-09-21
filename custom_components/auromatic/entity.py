"""Gemeinsame Basis aller Entitäten: Gerätezuordnung, Verfügbarkeit, Anlegen."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CIRCUITS, DOMAIN, ROOT_DEVICE
from .coordinator import AuromaticConfigEntry, AuromaticCoordinator


def _identity(coordinator: AuromaticCoordinator, circuit: str) -> dict[str, str]:
    """Software, Hardware und Seriennummer eines Kreises -- wenn er eines ist.

    Die fünf Kreise des Reglers (`hc`, `mc`, `hwc`, `cc`, `sc`) melden im Scan
    dieselbe Artikelnummer *und* denselben Zähler: es ist ein Gerät auf fünf
    Busadressen. Eine Seriennummer an jedem einzelnen würde in Home Assistant
    fünf Geräte behaupten, wo eines steht -- `serial_number` ist dort die
    Identität eines physischen Geräts. Sie steht deshalb nur am Regler selbst
    (in __init__.py) und an den beiden Teilnehmern, die wirklich eigene Geräte
    sind: Bedienteil und Therme.

    Erkennungsmerkmal ist nicht eine Liste, sondern die Sache selbst: nur wer
    ein eigenes `model` in const.CIRCUITS trägt, ist ein eigenes Gerät.
    """
    if "model" not in CIRCUITS[circuit]:
        return {}
    daten = coordinator.participant(circuit)
    identity: dict[str, str] = {}
    if sw := daten.get("sw"):
        identity["sw_version"] = sw
    if hw := daten.get("hw"):
        identity["hw_version"] = hw
    # Die Therme hat keine Seriennummer am Bus -- ihre Kennung kommt leer
    # zurück. Was sie hat, ist die Nummer ihrer Elektronik.
    if serial := (daten.get("serial") or daten.get("board_serial")):
        identity["serial_number"] = serial
    return identity


@dataclass(frozen=True, kw_only=True)
class CircuitMixin:
    """Bindet eine Entität an einen Kreis und eine ebusd-Nachricht.

    Als Mixin ausgelegt, damit die Plattformen ihre echte Basisklasse behalten
    (SensorEntityDescription und Verwandte) und deren Felder nicht neu
    definiert werden müssen.
    """

    circuit: str
    message: str
    field: int = 0
    status_field: int | None = None
    # Nur setzen, wo der Wert aus einem anderen Kreis kommt als das Gerät, an
    # dem die Entität hängt: der Speicherfühler oben steht im Warmwasserkreis
    # und im Solarkreis unter verschiedenen Namen, ist aber ein und derselbe
    # Fühler. Ihn zweimal zu pollen kostet einen Platz in der Warteschlange
    # für nichts; die Entität im Solarkreis liest deshalb aus `hwc`, behält
    # aber ihr Gerät, ihren Namen und ihre Historie.
    #
    # Ausschließlich für die Leseseite. Geschrieben wird immer auf `circuit`,
    # sonst ginge der Befehl an den falschen Teilnehmer -- tests/
    # test_translations.py prüft, dass beides nie zusammentrifft.
    source_circuit: str | None = None
    # Nur setzen, wo die Entität an ein anderes Gerät gehört, als ihr Kreis
    # nahelegt. `circuit` steckt in der `unique_id` und darf sich nie ändern --
    # die Gerätezuordnung darf sich sehr wohl ändern, denn sie ist reine
    # Darstellung.
    #
    # Zwei Fälle gibt es: Werte der Anlage statt eines Kreises (ROOT_DEVICE --
    # ebusd kennt sie nur unter einer Adresse, gemeint ist aber die ganze
    # Anlage), und Werte, die im Register eines fremden Kreises stehen: der
    # Solarertrag führt das Bedienteil, gesucht wird er beim Solar.
    device_circuit: str | None = None

    @property
    def source(self) -> str:
        """Der Kreis, aus dem der Wert kommt -- fast immer der eigene."""
        return self.source_circuit or self.circuit

    @property
    def device(self) -> str:
        """Das Gerät, an dem die Entität hängt -- fast immer der eigene Kreis."""
        return self.device_circuit or self.circuit


@dataclass(frozen=True, kw_only=True)
class CircuitDescription(EntityDescription, CircuitMixin):
    """Für Plattformen ohne eigene Beschreibungsklasse."""


class AuromaticEntity(CoordinatorEntity[AuromaticCoordinator]):
    """Bindet eine Entität an genau einen Bus-Teilnehmer."""

    _attr_has_entity_name = True
    entity_description: CircuitDescription

    def __init__(
        self,
        coordinator: AuromaticCoordinator,
        description: CircuitDescription,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{description.circuit}_{description.key}"
        # Der Beschreibungsschlüssel ist in aller Regel zugleich der
        # Übersetzungsschlüssel: die Namen stehen in translations/, nicht im
        # Code. Fehlt dort ein Eintrag, bliebe die Entität namenlos --
        # tests/test_translations.py prüft das.
        #
        # Auseinander fallen beide nur, wo derselbe Wert in verschiedenen
        # Kreisen verschieden heißt: `key` ist Teil der `unique_id` und muss
        # deshalb stehen bleiben, die Beschriftung darf sich trotzdem
        # unterscheiden. Der Zirkulationskreis nennt seinen Dauerbetrieb "Ein",
        # die Heizkreise nennen ihren "Heizen" -- so steht es am Bedienteil.
        self._attr_translation_key = description.translation_key or description.key

        if description.device == ROOT_DEVICE:
            # Der Regler selbst; angelegt wird er in __init__.py mit Namen und
            # Softwarestand. Hier genügt die Kennung, alles Weitere führt das
            # Geräteregister bereits.
            self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry_id)})
        else:
            circuit = CIRCUITS[description.device]
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{entry_id}_{description.device}")},
                # Voreinstellung ist der Regler -- die Kreise sind Teile
                # von ihm und tragen seinen Namen. Nur die Therme nicht.
                name=circuit.get("device_name", f"auroMATIC {circuit['name']}"),
                manufacturer="Vaillant",
                # Voreinstellung ist der Regler selbst -- alle Kreise sind
                # Teile von ihm. Therme und Bedienteil sind eigene Geräte am
                # Bus und nennen ihr Modell deshalb in const.CIRCUITS.
                model=circuit.get(
                    "model",
                    f"auroMATIC 620/3 ({description.device} @ {circuit['address']})",
                ),
                via_device=(DOMAIN, entry_id),
                **_identity(coordinator, description.device),
            )

    @property
    def raw_message(self) -> str | None:
        """Der unzerlegte Wert der Nachricht."""
        return self.coordinator.message(
            self.entity_description.source, self.entity_description.message
        )

    @property
    def raw_value(self) -> str | None:
        """Der aufbereitete Rohwert dieser Entität."""
        return self.coordinator.value(
            self.entity_description.source,
            self.entity_description.message,
            self.entity_description.field,
            self.entity_description.status_field,
        )

    @property
    def available(self) -> bool:
        return super().available and self.raw_message is not None


def has_value(coordinator: AuromaticCoordinator, description: CircuitMixin) -> bool:
    """Antwortet das Register dieser Beschreibung mit einem brauchbaren Wert?

    Der Regelfall für async_add_available: ein Feld, das sich zerlegen lässt,
    und -- wo `status_field` gesetzt ist -- ein Fühler, der auch angeschlossen
    ist. Zwei Plattformen brauchen etwas anderes und bringen es selbst mit.
    """
    return coordinator.value(
        description.source, description.message,
        description.field, description.status_field,
    ) is not None


@callback
def async_add_available[D: CircuitMixin](
    entry: AuromaticConfigEntry,
    async_add_entities: AddEntitiesCallback,
    descriptions: Iterable[D],
    build: Callable[[D], AuromaticEntity],
    ready: Callable[[AuromaticCoordinator, D], bool] = has_value,
) -> None:
    """Entitäten anlegen -- beim Setup und später, sobald ihr Register antwortet.

    Angelegt wird nur, was tatsächlich einen Wert liefert: ein nicht verbauter
    Fühler meldet `cutoff` und soll keine Entität bekommen. Die Prüfung nur
    einmal beim Setup zu machen, war der Fehler -- dann hängt für immer ab,
    was ebusd in genau dieser Sekunde wusste.

    Am 2026-09-19 um 20:25 hat das 24 Entitäten gekostet: Home Assistant
    startete, während ebusd noch scannte, und die beiden zuletzt geladenen
    Adressen 0x50 (`mc`) und 0xec (`sc`) waren noch nicht an der Reihe. Ihre
    Register antworteten Minuten später wieder -- die Entitäten dazu gab es
    bis zum Neuladen der Integration 34 Stunden später nicht. Ohne Fehler im
    Protokoll: es fehlte ja nichts, es war nie da. Derselbe Fall trifft jeden
    Teilnehmer, der nach dem Setup dazukommt; der Kessel war bis zum
    2026-09-04 stromlos und brauchte genau deshalb ein Neuladen.

    Das Muster ist das der HA-Qualitätsstufe Gold (`dynamic-devices`): beim
    Setup anlegen, was da ist, und am Koordinator lauschen für den Rest. Das
    Abmelden hängt an `entry`, weil sich fünf Plattformen einen Koordinator
    teilen -- ohne das überlebt der Rückruf das Entladen.

    Der Rückruf wird auch dann angemeldet, wenn die Plattform noch gar keine
    Entität bekommt, und das ist der Kern der Sache: ein DataUpdateCoordinator
    ohne Zuhörer stellt seinen Abruf ein. Ohne diese Anmeldung liefe für einen
    beim Setup vollständig abwesenden Kreis nie wieder ein Abruf -- und damit
    entstünde seine Entität auch nie.
    """
    coordinator = entry.runtime_data
    candidates = tuple(descriptions)
    # Angelegt wird je Kreis und Schlüssel genau einmal -- dasselbe Paar, das
    # die `unique_id` trägt. Der Schlüssel allein genügt nicht: `mode` gibt es
    # in Heiz-, Mischer- und Zirkulationskreis.
    known: set[tuple[str, str]] = set()

    @callback
    def _add_new() -> None:
        new = [
            description for description in candidates
            if (description.circuit, description.key) not in known
            and ready(coordinator, description)
        ]
        if not new:
            return
        known.update((description.circuit, description.key) for description in new)
        async_add_entities(build(description) for description in new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))
