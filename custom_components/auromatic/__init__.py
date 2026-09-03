"""Vaillant auroMATIC 620/3 über ebusd."""

from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_PORT, CONF_SCAN_INTERVAL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .const import DEFAULT_PORT, DEFAULT_SCAN_INTERVAL, DOMAIN
from .coordinator import AuromaticConfigEntry, AuromaticCoordinator
from .ebusd import EbusdClient, EbusdError

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.WATER_HEATER,
]


async def async_setup_entry(hass: HomeAssistant, entry: AuromaticConfigEntry) -> bool:
    """Integration für einen konfigurierten ebusd einrichten."""
    client = EbusdClient(entry.data[CONF_HOST], entry.data.get(CONF_PORT, DEFAULT_PORT))

    try:
        version = await client.version()
    except EbusdError as err:
        await client.close()
        raise ConfigEntryNotReady(f"ebusd nicht erreichbar: {err}") from err

    coordinator = AuromaticCoordinator(
        hass,
        entry,
        client,
        entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
    )
    entry.runtime_data = coordinator

    # Vor dem ersten Abruf anmelden, nicht danach: 'read -m' liefert bei leerem
    # Zwischenspeicher frisch vom Bus und füllt ihn damit gleich mit. Ohne das
    # sähe der erste Abruf nach einem ebusd-Neustart einen leeren Cache -- und
    # Entitäten entstehen nur dort, wo beim Setup ein Wert vorliegt.
    # Danach hält der Koordinator die Anmeldung selbst nach: er prüft bei jedem
    # Abruf, ob unsere Register noch in der Poll-Liste von ebusd stehen.
    await coordinator.async_apply_poll()
    await coordinator.async_config_entry_first_refresh()
    # Und nachfassen, was der erste Abruf nicht hatte: Entitäten entstehen nur
    # dort, wo beim Setup ein Wert vorliegt, und ein kalter Zwischenspeicher
    # sieht aus wie ein fehlender Fühler.
    await coordinator.async_warm_cache()

    # Der Regler selbst als übergeordnetes Gerät -- die Kreise hängen per
    # via_device daran, damit die Geräteseite die Bus-Struktur abbildet.
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer="Vaillant",
        name="auroMATIC 620/3",
        model="auroMATIC 620/3",
        sw_version=version,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AuromaticConfigEntry) -> bool:
    """Integration abbauen und die Verbindung freigeben."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.client.close()
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: AuromaticConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
