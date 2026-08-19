"""Diagnostics support for GWM ANZ."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_PASSWORD, CONF_SECURITY_PASSWORD, DOMAIN

TO_REDACT = {
    CONF_PASSWORD,
    CONF_SECURITY_PASSWORD,
    "access_token",
    "refresh_token",
    "accessToken",
    "refreshToken",
    "token",
}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    data = {
        "entry": {
            "data": dict(entry.data),
            "options": dict(entry.options),
            "title": entry.title,
            "unique_id": entry.unique_id,
            "domain": DOMAIN,
        },
        "vehicles": entry.runtime_data.coordinator.data if entry.runtime_data else None,
    }
    return async_redact_data(data, TO_REDACT)
