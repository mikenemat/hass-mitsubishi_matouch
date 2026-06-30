"""Diagnostic/telemetry sensors for Mitsubishi MA Touch thermostats.

Per-thermostat BLE connection health, so the persistent-connection + proxy design
can be characterized over time in HA history: connection uptime, reconnect and
disconnect counts, per-poll latency, the serving proxy, and link RSSI.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfTime,
)
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


@dataclass(frozen=True, kw_only=True)
class MASensorDescription(SensorEntityDescription):
    """Describes an MA Touch diagnostic sensor."""

    value_fn: Callable[[MACoordinator], float | int | str | None]
    # Optional extra attributes (e.g. the full capability detail / per-head info).
    attrs_fn: Callable[[MACoordinator], dict | None] | None = None


def _caps(c: MACoordinator):
    return c.capabilities


def _caps_summary(c: MACoordinator) -> str | None:
    """Static, human-readable state for the Capabilities sensor (the full breakdown
    lives in its attributes). Stays constant per unit, so it doesn't grow the recorder."""
    caps = _caps(c)
    if caps is None:
        return None
    n = caps.num_indoor_units
    return f"{n} indoor unit{'s' if n != 1 else ''}"


# Single capability sensor (DIAGNOSTIC). State is a static summary; the FULL capability
# breakdown (modes, fan steps, vane, hold/louver/vent/move-eye support, per-head info)
# is in its attributes, so everything is visible in one place (more-info, Dev Tools,
# templates) without a swarm of entities — and being static it won't bloat history.
# coordinator.capabilities is None until the device-info blob is fetched (lazily, shortly
# after the first poll), so this reads 'unknown' until then.
CAP_SENSORS: tuple[MASensorDescription, ...] = (
    MASensorDescription(
        key="capabilities",
        name="Capabilities",
        icon="mdi:hvac",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_caps_summary,
        attrs_fn=lambda c: _caps(c).as_dict() if _caps(c) else None,
    ),
)


SENSORS: tuple[MASensorDescription, ...] = (
    MASensorDescription(
        key="connection_uptime",
        name="Connection uptime",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: round(c.connection_uptime) if c.connection_uptime is not None else None,
    ),
    MASensorDescription(
        key="reconnects",
        name="Reconnects",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.reconnects,
    ),
    MASensorDescription(
        key="disconnects",
        name="Disconnects",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.disconnects,
    ),
    MASensorDescription(
        key="poll_latency",
        name="Poll latency",
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: round(c.last_poll_duration * 1000) if c.last_poll_duration is not None else None,
    ),
    MASensorDescription(
        key="active_proxy",
        name="Active proxy",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.active_proxy,
    ),
    MASensorDescription(
        key="signal_strength",
        name="Signal strength",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.active_rssi,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MAConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up diagnostic sensors per thermostat subentry, now and as ones are added."""

    @callback
    def _add(subentry_id: str) -> None:
        coordinator = entry.runtime_data.coordinators.get(subentry_id)
        if coordinator is None:
            return
        async_add_entities(
            [MASensor(coordinator, description) for description in (*SENSORS, *CAP_SENSORS)],
            config_subentry_id=subentry_id,
        )

    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_TYPE_THERMOSTAT:
            _add(subentry.subentry_id)

    entry.async_on_unload(
        async_dispatcher_connect(hass, f"{SIGNAL_NEW_THERMOSTAT}_{entry.entry_id}", _add)
    )


class MASensor(CoordinatorEntity[MACoordinator], SensorEntity):
    """A single MA Touch diagnostic sensor."""

    _attr_has_entity_name = True
    entity_description: MASensorDescription

    def __init__(self, coordinator: MACoordinator, description: MASensorDescription) -> None:
        """Initialize the sensor."""

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
    def native_value(self) -> float | int | str | None:
        """Return the current telemetry value."""

        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict | None:
        """Optional extra detail (e.g. the full capability breakdown + per-head info)."""

        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator)

    @property
    def available(self) -> bool:
        """Keep telemetry readable even while the device is unreachable."""

        return True
