"""Vaillant auroMATIC 620/3 ueber ebusd."""

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
    """Integration fuer einen konfigurierten ebusd einrichten."""
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
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # Der Regler selbst als uebergeordnetes Geraet -- die Kreise haengen per
    # via_device daran, damit die Geraeteseite die Bus-Struktur abbildet.
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer="Vaillant",
        name="auroMATIC 620/3",
        model="auroMATIC 620/3",
        sw_version=version,
        configuration_url=f"http://{entry.data[CONF_HOST]}",
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
