"""Switch entities for Mitsubishi MA Touch thermostats.

Currently the HOLD function (keep the current setpoint / suspend the schedule), which
the device exposes over BLE and reports back in its status frame.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
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
    SIGNAL_NEW_THERMOSTAT,
    SUBENTRY_TYPE_THERMOSTAT,
)
from .coordinator import MACoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MAConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the HOLD switch per thermostat subentry, now and as ones are added."""

    @callback
    def _add(subentry_id: str) -> None:
        coordinator = entry.runtime_data.coordinators.get(subentry_id)
        if coordinator is None:
            return
        async_add_entities([MAHoldSwitch(coordinator)], config_subentry_id=subentry_id)

    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_TYPE_THERMOSTAT:
            _add(subentry.subentry_id)

    entry.async_on_unload(
        async_dispatcher_connect(hass, f"{SIGNAL_NEW_THERMOSTAT}_{entry.entry_id}", _add)
    )


class MAHoldSwitch(CoordinatorEntity[MACoordinator], SwitchEntity):
    """HOLD on/off for an MA Touch thermostat."""

    _attr_has_entity_name = True
    _attr_name = "Hold"
    _attr_icon = "mdi:lock-clock"

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
        """Whether HOLD is active (None until the first status is read)."""

        status = self.coordinator.data
        return bool(status.hold) if status is not None else None

    @property
    def available(self) -> bool:
        """Mirror the climate entity's availability so the control greys out when the
        unit is unreachable (and rides out a brief BLE hiccup)."""

        if self.coordinator.last_update_success:
            return True
        if self.coordinator.is_stale:
            return False
        return self.coordinator.consecutive_failures < 2

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable HOLD."""

        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable HOLD."""

        await self._async_set(False)

    async def _async_set(self, on: bool) -> None:
        try:
            await self.coordinator.async_set_hold(on)
        except MAException as ex:
            raise ServiceValidationError(f"Failed to set hold: {ex}") from ex
