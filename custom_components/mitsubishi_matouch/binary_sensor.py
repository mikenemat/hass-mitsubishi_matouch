"""Capability binary sensors for Mitsubishi MA Touch thermostats.

One DIAGNOSTIC binary sensor per supported control axis, so each unit's hardware
capabilities (which genuinely differ across the fleet — e.g. only the CT01MAU units
support hold; only some have a vane) are individually visible and usable in automations
and dashboards. These are static (they reflect what the device advertises in its
capability blob, not live state); they read as 'unknown' until the blob is fetched
(lazily, shortly after the first poll).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo, format_mac
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MAConfigEntry
from .btmatouch.capabilities import Capabilities
from .const import (
    DEVICE_MODEL,
    DEVICE_MODEL_ID,
    MANUFACTURER,
    SIGNAL_NEW_THERMOSTAT,
    SUBENTRY_TYPE_THERMOSTAT,
)
from .coordinator import MACoordinator


@dataclass(frozen=True, kw_only=True)
class MACapBinaryDescription(BinarySensorEntityDescription):
    """Describes an MA Touch capability binary sensor."""

    value_fn: Callable[[Capabilities], bool]


CAP_BINARY_SENSORS: tuple[MACapBinaryDescription, ...] = (
    MACapBinaryDescription(
        key="supports_hold",
        name="Supports hold",
        icon="mdi:pause-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda caps: caps.hold,
    ),
    MACapBinaryDescription(
        key="supports_swing",
        name="Supports vane swing",
        icon="mdi:arrow-oscillating",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda caps: caps.supports_swing,
    ),
    MACapBinaryDescription(
        key="supports_louver",
        name="Supports louver",
        icon="mdi:blinds-horizontal",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda caps: caps.louver,
    ),
    MACapBinaryDescription(
        key="supports_ventilation",
        name="Supports ventilation",
        icon="mdi:air-filter",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda caps: caps.lossnai,
    ),
    MACapBinaryDescription(
        key="supports_move_eye",
        name="Supports Move-Eye",
        icon="mdi:eye-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda caps: caps.move_eye,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MAConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up capability binary sensors per thermostat subentry, now and as ones are added."""

    @callback
    def _add(subentry_id: str) -> None:
        coordinator = entry.runtime_data.coordinators.get(subentry_id)
        if coordinator is None:
            return
        async_add_entities(
            [MACapBinarySensor(coordinator, description) for description in CAP_BINARY_SENSORS],
            config_subentry_id=subentry_id,
        )

    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_TYPE_THERMOSTAT:
            _add(subentry.subentry_id)

    entry.async_on_unload(
        async_dispatcher_connect(hass, f"{SIGNAL_NEW_THERMOSTAT}_{entry.entry_id}", _add)
    )


class MACapBinarySensor(CoordinatorEntity[MACoordinator], BinarySensorEntity):
    """A single MA Touch capability binary sensor."""

    _attr_has_entity_name = True
    entity_description: MACapBinaryDescription

    def __init__(self, coordinator: MACoordinator, description: MACapBinaryDescription) -> None:
        """Initialize the capability binary sensor."""

        super().__init__(coordinator)
        self.entity_description = description
        mac = coordinator.mac_address
        self._attr_unique_id = f"matouch_{format_mac(mac)}_{description.key}"
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, format_mac(mac))},
            name=coordinator.device_name,
            manufacturer=MANUFACTURER,
            model=DEVICE_MODEL,
            model_id=DEVICE_MODEL_ID,
        )

    @property
    def is_on(self) -> bool | None:
        """Whether the unit supports this axis (None until caps are fetched)."""

        caps = self.coordinator.capabilities
        if caps is None:
            return None
        return self.entity_description.value_fn(caps)

    @property
    def available(self) -> bool:
        """Keep readable even while the device is unreachable (static capability)."""

        return True
