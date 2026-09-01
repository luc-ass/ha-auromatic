"""Einrichtungsdialog: ebusd-Adresse und Abrufintervall."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv

from .const import DEFAULT_PORT, DEFAULT_SCAN_INTERVAL, DOMAIN
from .coordinator import AuromaticConfigEntry
from .ebusd import EbusdClient, EbusdError

_LOGGER = logging.getLogger(__name__)

# Als Add-on läuft ebusd in einem eigenen Container im selben Docker-Netz wie
# Home Assistant. Der Supervisor vergibt als Hostnamen den Add-on-Slug mit
# Bindestrichen statt Unterstrichen -- genau der Name, der im Add-on-Terminal
# im Prompt steht. ebusd meldet sich nicht per mDNS an, deshalb wird geraten
# statt entdeckt.
_STATIC_CANDIDATES: tuple[str, ...] = (
    "core-ebusd",
    "local-ebusd",
    "addon_ebusd",
    "ebusd",
    "localhost",
)


async def _probe(host: str, port: int) -> str | None:
    """Prüft, ob unter dieser Adresse ein ebusd antwortet."""
    client = EbusdClient(host, port, timeout=3.0)
    try:
        return await client.version()
    except (EbusdError, OSError, asyncio.TimeoutError):
        return None
    finally:
        await client.close()


async def _addon_hostnames(hass) -> list[str]:
    """Hostnamen aus der Add-on-Liste des Supervisors ableiten.

    Nur auf Home Assistant OS und Supervised vorhanden; auf allen anderen
    Installationsarten schlägt der Import fehl und es bleibt beim Raten.
    """
    try:
        from homeassistant.components.hassio import get_addons_info, is_hassio

        if not is_hassio(hass):
            return []
        addons = get_addons_info(hass) or {}
    except Exception as err:  # noqa: BLE001 - Supervisor ist optional
        _LOGGER.debug("Add-on-Liste nicht verfügbar: %s", err)
        return []
    return [slug.replace("_", "-") for slug in addons if "ebusd" in slug.lower()]


async def _find_ebusd(hass, port: int) -> str | None:
    """Die wahrscheinlichsten Adressen gleichzeitig anklopfen."""
    candidates = list(dict.fromkeys([*await _addon_hostnames(hass), *_STATIC_CANDIDATES]))
    results = await asyncio.gather(*(_probe(host, port) for host in candidates))
    for host, version in zip(candidates, results):
        if version is not None:
            _LOGGER.debug("ebusd %s unter %s gefunden", version, host)
            return host
    return None


STEP_USER = vol.Schema(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): cv.port,
    }
)


class AuromaticConfigFlow(ConfigFlow, domain=DOMAIN):
    """Führt durch die Ersteinrichtung."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is None:
            # Vorschlag suchen, damit im Normalfall nur noch bestätigt werden muss.
            found = await _find_ebusd(self.hass, DEFAULT_PORT)
            schema = self.add_suggested_values_to_schema(
                STEP_USER, {CONF_HOST: found} if found else {}
            )
            return self.async_show_form(step_id="user", data_schema=schema)

        if user_input is not None:
            self._async_abort_entries_match(
                {CONF_HOST: user_input[CONF_HOST], CONF_PORT: user_input[CONF_PORT]}
            )
            client = EbusdClient(user_input[CONF_HOST], user_input[CONF_PORT])
            try:
                await client.version()
            except EbusdError:
                errors["base"] = "cannot_connect"
            finally:
                await client.close()

            if not errors:
                return self.async_create_entry(title="auroMATIC 620/3", data=user_input)

        return self.async_show_form(step_id="user", data_schema=STEP_USER, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(entry: AuromaticConfigEntry) -> AuromaticOptionsFlow:
        return AuromaticOptionsFlow()


class AuromaticOptionsFlow(OptionsFlow):
    """Nachträglich das Abrufintervall anpassen."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=15, max=900)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
