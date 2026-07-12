"""Diagnostics export for Mitsubishi MA Touch.

One-click "Download diagnostics" JSON per entry: current connection metrics and
recent telemetry for every thermostat subentry, plus proxy assignments. PINs are
redacted (they live in each subentry's data).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .models import MAConfigEntry

TO_REDACT = {"pin"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MAConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for the parent config entry."""

    domain_data = hass.data.get(DOMAIN, {})
    balancer = domain_data.get("balancer")
    telemetry = domain_data.get("telemetry")
    coordinators = entry.runtime_data.coordinators if entry.runtime_data else {}

    devices: list[dict[str, Any]] = []
    for subentry_id, coordinator in coordinators.items():
        last_poll = coordinator.last_poll_duration
        devices.append(
            {
                "subentry_id": subentry_id,
                "name": coordinator.device_name,
                "mac": coordinator.mac_address,
                "available": coordinator.last_update_success,
                "firmware_version": coordinator.firmware_version,
                "software_version": coordinator.software_version,
                "connection_uptime_s": coordinator.connection_uptime,
                "reconnects": coordinator.reconnects,
                "disconnects": coordinator.disconnects,
                "last_poll_duration_ms": round(last_poll * 1000) if last_poll is not None else None,
                "active_proxy": coordinator.active_proxy,
                "active_rssi": coordinator.active_rssi,
                "login_responses": coordinator.login_responses,
                "device_info_hex": coordinator.device_info_hex,
                "capabilities": (
                    coordinator.capabilities.as_dict() if coordinator.capabilities else None
                ),
                "recent_telemetry": telemetry.recent(coordinator.mac_address) if telemetry else [],
            }
        )

    return {
        "entry": {
            "title": entry.title,
            "options": dict(entry.options),
            "subentries": [
                {
                    "title": subentry.title,
                    "data": async_redact_data(dict(subentry.data), TO_REDACT),
                }
                for subentry in entry.subentries.values()
            ],
        },
        "proxy_assignments": balancer.assignments if balancer else {},
        "telemetry_path": telemetry.path if telemetry else None,
        "devices": devices,
    }
