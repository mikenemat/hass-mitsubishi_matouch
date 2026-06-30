"""Hold switch for Mitsubishi MA Touch thermostats.

A plain On/Off switch for HOLD (keep the current setpoint / suspend the schedule),
created only on units whose capability blob advertises hold support (e.g. the CT01MAU
units; not Theater's CT01MA). HA climate cards can't host a switch and "hold" is an
awkward climate preset, so this is a dedicated switch entity instead. It is created once
the unit's capabilities are known (SIGNAL_CAPS_LOADED), so it never appears as a dead
control on a unit that can't hold.
"""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
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
    """Create a Hold switch per hold-capable thermostat, once capabilities are known."""

    created: set[str] = set()

    @callback
    def _maybe_add(subentry_id: str) -> None:
        if subentry_id in created:
            return
        coordinator = entry.runtime_data.coordinators.get(subentry_id)
        if coordinator is None:
            return
        caps = coordinator.capabilities
        if caps is None or not caps.hold:
            return  # caps not loaded yet, or this unit can't hold
        created.add(subentry_id)
        async_add_entities([MAHoldSwitch(coordinator)], config_subentry_id=subentry_id)

    # Caps may already be cached (e.g. on a reload); otherwise SIGNAL_CAPS_LOADED fires
    # once the device-info blob is fetched shortly after the first poll.
    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_TYPE_THERMOSTAT:
            _maybe_add(subentry.subentry_id)

    entry.async_on_unload(
        async_dispatcher_connect(hass, f"{SIGNAL_CAPS_LOADED}_{entry.entry_id}", _maybe_add)
    )
    entry.async_on_unload(
        async_dispatcher_connect(hass, f"{SIGNAL_NEW_THERMOSTAT}_{entry.entry_id}", _maybe_add)
    )


class MAHoldSwitch(CoordinatorEntity[MACoordinator], SwitchEntity):
    """HOLD on/off for one thermostat."""

    _attr_has_entity_name = True
    _attr_name = "Hold"
    _attr_icon = "mdi:pause-circle-outline"

    def __init__(self, coordinator: MACoordinator) -> None:
        """Initialize the hold switch."""

        super().__init__(coordinator)
        mac = coordinator.mac_address
        self._attr_unique_id = f"matouch_{format_mac(mac)}_hold"
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, format_mac(mac))},
            name=coordinator.device_name,
            manufacturer=MANUFACTURER,
            model=DEVICE_MODEL,
            model_id=DEVICE_MODEL_ID,
        )

    @property
    def is_on(self) -> bool | None:
        """Whether HOLD is currently active (None until the first status)."""

        status = self.coordinator.data
        if status is None:
            return None
        return bool(getattr(status, "hold", False))

    async def async_turn_on(self, **kwargs) -> None:
        """Enable HOLD."""

        try:
            await self.coordinator.async_set_hold(True)
        except MAException as ex:
            raise HomeAssistantError(f"Failed to set hold: {ex}") from ex

    async def async_turn_off(self, **kwargs) -> None:
        """Disable HOLD."""

        try:
            await self.coordinator.async_set_hold(False)
        except MAException as ex:
            raise HomeAssistantError(f"Failed to clear hold: {ex}") from ex

    @property
    def available(self) -> bool:
        """Mirror the climate entity's availability (tolerate one transient blip)."""

        c = self.coordinator
        if c.last_update_success:
            return True
        if c.is_stale:
            return False
        return c.consecutive_failures < 2
