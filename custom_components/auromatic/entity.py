"""Gemeinsame Basis aller Entitäten: Gerätezuordnung und Verfügbarkeit."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CIRCUITS, DOMAIN, ROOT_DEVICE
from .coordinator import AuromaticCoordinator


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
                name=f"auroMATIC {circuit['name']}",
                manufacturer="Vaillant",
                # Voreinstellung ist der Regler selbst -- alle Kreise sind
                # Teile von ihm. Nur der Kessel ist ein eigenes Gerät am Bus
                # und nennt sein Modell deshalb in const.CIRCUITS.
                model=circuit.get(
                    "model",
                    f"auroMATIC 620/3 ({description.device} @ {circuit['address']})",
                ),
                via_device=(DOMAIN, entry_id),
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
