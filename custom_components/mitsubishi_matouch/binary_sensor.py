"""Fault (problem) binary sensor for Mitsubishi MA Touch thermostats.

Reports whether a thermostat is in a device-fault state: connected and authenticated,
but persistently rejecting operation/settings commands with a device error (0x09) — i.e.
stuck on an error/startup screen (like the E4 startup fault). This is the machine-readable
companion to the "thermostat fault" Repairs notice, for automations/dashboards/history.
Created for every thermostat (any unit can fault), and stays available even while the
unit's polls are failing so it can actually report the problem.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo, format_mac
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MAConfigEntry
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
    """Set up a Fault problem sensor per thermostat subentry."""

    @callback
    def _add(subentry_id: str) -> None:
        coordinator = entry.runtime_data.coordinators.get(subentry_id)
        if coordinator is None:
            return
        async_add_entities([MAFaultSensor(coordinator)], config_subentry_id=subentry_id)

    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_TYPE_THERMOSTAT:
            _add(subentry.subentry_id)

    entry.async_on_unload(
        async_dispatcher_connect(hass, f"{SIGNAL_NEW_THERMOSTAT}_{entry.entry_id}", _add)
    )


class MAFaultSensor(CoordinatorEntity[MACoordinator], BinarySensorEntity):
    """On when the thermostat is reporting a persistent device fault."""

    _attr_has_entity_name = True
    _attr_name = "Fault"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: MACoordinator) -> None:
        """Initialize the fault sensor."""

        super().__init__(coordinator)
        mac = coordinator.mac_address
        self._attr_unique_id = f"matouch_{format_mac(mac)}_fault"
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, format_mac(mac))},
            name=coordinator.device_name,
            manufacturer=MANUFACTURER,
            model=DEVICE_MODEL,
            model_id=DEVICE_MODEL_ID,
        )

    @property
    def is_on(self) -> bool:
        """True when the unit has a persistent device fault."""

        return self.coordinator.is_device_faulted

    @property
    def extra_state_attributes(self) -> dict | None:
        """Surface the device error result code + trailing detail byte (e.g. result 0x09,
        detail 0x78 for the observed E4 startup fault) for diagnostics; None when not
        faulted. The specific on-screen code (E4/E2/…) isn't in this response — it's on the
        unit's display — so these are the raw BLE-visible error signals."""

        if not self.coordinator.is_device_faulted:
            return None
        attrs: dict = {}
        result = self.coordinator.device_fault_result
        detail = self.coordinator.device_fault_detail
        if result is not None:
            attrs["error_result"] = f"0x{result:02x}"
        if detail is not None:
            attrs["error_detail"] = f"0x{detail:02x}"
        return attrs or None

    @property
    def available(self) -> bool:
        """Always readable — a faulted unit's polls fail, but the fault sensor must stay
        available to report the problem rather than going unavailable itself."""

        return True
