"""Diagnosedaten -- der komplette Registerbestand, wie ebusd ihn liefert."""

from __future__ import annotations

from typing import Any

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .coordinator import AuromaticConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AuromaticConfigEntry
) -> dict[str, Any]:
    """Alles ausgeben, was zur Fehlersuche noetig ist."""
    coordinator = entry.runtime_data
    try:
        info = await coordinator.client.command("info")
    except Exception as err:  # noqa: BLE001 - Diagnose darf nie scheitern
        info = [f"info fehlgeschlagen: {err}"]

    return {
        "host": entry.data.get(CONF_HOST),
        "ebusd_info": info,
        "circuits": coordinator.data,
    }
