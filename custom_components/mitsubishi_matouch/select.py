"""Vane (vertical airflow) select for Mitsubishi MA Touch thermostats.

A dedicated "Vane" select exposing the unit's airflow positions (auto / horizontal /
down 20% / down 60% / down 80% / down 100% / swing). This replaces the climate "Swing
mode" control, whose label is hard-coded by HA core and is a misnomer for fixed vane
positions. Created only on units that have a controllable vane, once capabilities are
known (SIGNAL_CAPS_LOADED).
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo, format_mac
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MAConfigEntry
from .btmatouch.exceptions import MAException
from .const import (
    DEVICE_MODEL,
    DEVICE_MODEL_ID,
    MANUFACTURER,
    MA_VANE_VALUE_TO_HA,
    HA_TO_MA_VANE,
    SIGNAL_CAPS_LOADED,
    SIGNAL_NEW_THERMOSTAT,
    SUBENTRY_TYPE_THERMOSTAT,
)
from .coordinator import MACoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MAConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a Vane select per vane-capable thermostat, once capabilities are known."""

    created: set[str] = set()

    @callback
    def _maybe_add(subentry_id: str) -> None:
        if subentry_id in created:
            return
        coordinator = entry.runtime_data.coordinators.get(subentry_id)
        if coordinator is None:
            return
        caps = coordinator.capabilities
        if caps is None or not caps.supports_swing:
            return  # caps not loaded yet, or this unit has no vane
        created.add(subentry_id)
        async_add_entities([MAVaneSelect(coordinator)], config_subentry_id=subentry_id)

    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_TYPE_THERMOSTAT:
            _maybe_add(subentry.subentry_id)

    entry.async_on_unload(
        async_dispatcher_connect(hass, f"{SIGNAL_CAPS_LOADED}_{entry.entry_id}", _maybe_add)
    )
    entry.async_on_unload(
        async_dispatcher_connect(hass, f"{SIGNAL_NEW_THERMOSTAT}_{entry.entry_id}", _maybe_add)
    )


class MAVaneSelect(CoordinatorEntity[MACoordinator], SelectEntity):
    """Vane (vertical airflow direction) position for one thermostat."""

    _attr_has_entity_name = True
    _attr_name = "Vane"
    _attr_icon = "mdi:arrow-oscillating"

    def __init__(self, coordinator: MACoordinator) -> None:
        """Initialize the vane select. Options are static per unit (created only once
        capabilities are known, so vane_modes() is available here)."""

        super().__init__(coordinator)
        mac = coordinator.mac_address
        self._attr_unique_id = f"matouch_{format_mac(mac)}_vane"
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, format_mac(mac))},
            name=coordinator.device_name,
            manufacturer=MANUFACTURER,
            model=DEVICE_MODEL,
            model_id=DEVICE_MODEL_ID,
        )
        caps = coordinator.capabilities
        self._attr_options = caps.vane_modes() if caps else []

    @property
    def current_option(self) -> str | None:
        """Current vane position (None until the first status). Mapped by wire value to
        dodge the MAVaneMode NONE/STEP_5 == 0 alias."""

        status = self.coordinator.data
        if status is None:
            return None
        return MA_VANE_VALUE_TO_HA.get(int(status.vane_mode))

    async def async_select_option(self, option: str) -> None:
        """Set the vane to the chosen position."""

        vane_mode = HA_TO_MA_VANE.get(option)
        if vane_mode is None:
            raise HomeAssistantError(f"Unknown vane position: {option}")
        try:
            await self.coordinator.async_set_vane_mode(vane_mode)
        except MAException as ex:
            raise HomeAssistantError(f"Failed to set vane: {ex}") from ex

    @property
    def available(self) -> bool:
        """Mirror the climate entity's availability (tolerate one transient blip)."""

        c = self.coordinator
        if c.last_update_success:
            return True
        if c.is_stale:
            return False
        return c.consecutive_failures < 2
