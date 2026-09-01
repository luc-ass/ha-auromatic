"""Diagnosedaten -- der komplette Registerbestand, wie ebusd ihn liefert."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .coordinator import AuromaticConfigEntry

# Diagnosedaten werden in Fehlerberichte kopiert. Die Adresse des Dienstes ist
# zwar kein Geheimnis, gehört aber nicht ungefragt in ein öffentliches Ticket.
TO_REDACT = {CONF_HOST}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AuromaticConfigEntry
) -> dict[str, Any]:
    """Alles ausgeben, was zur Fehlersuche nötig ist."""
    coordinator = entry.runtime_data
    try:
        info = await coordinator.client.command("info")
    except Exception as err:  # noqa: BLE001 - Diagnose darf nie scheitern
        info = [f"info fehlgeschlagen: {err}"]

    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "ebusd_info": info,
        "circuits": coordinator.data,
    }
