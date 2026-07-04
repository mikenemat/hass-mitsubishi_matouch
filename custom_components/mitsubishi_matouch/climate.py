"""Platform for Mitsubishi MA Touch climate entities."""

from typing import Any

from homeassistant.components.climate import (
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_HALVES, PRECISION_WHOLE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo, format_mac
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .btmatouch.const import MA_MIN_TEMP, MA_MAX_TEMP, MAOperationMode
from .btmatouch.exceptions import MAException
from .coordinator import MACoordinator
from .temperature import from_display_setpoint, to_display_room, to_display_setpoint
from . import MAConfigEntry
from .const import (
    DEVICE_MODEL,
    DEVICE_MODEL_ID,
    MANUFACTURER,
    MA_TO_HA_HVAC,
    HA_TO_MA_HVAC,
    MA_TO_HA_FAN,
    HA_TO_MA_FAN,
    MA_VANE_VALUE_TO_HA,
    HA_TO_MA_VANE,
    MA_RL_VALUE_TO_HA,
    HA_TO_MA_RL,
    MA_UNIT_STATE_TO_HVAC_ACTION,
    MA_UNIT_STATE_NAMES,
    SIGNAL_NEW_THERMOSTAT,
    SUBENTRY_TYPE_THERMOSTAT,
)


def _model_id_for(sw_version: str | None) -> str:
    """Per-unit technical model id from the software-version string (e.g.
    'CT01MA_07.02' -> PAR-CT01MA, 'CT01MAU_01.61' -> PAR-CT01MAU). Check the longer
    'CT01MAU' token first since 'CT01MA' is a substring of it."""
    if sw_version and "CT01MAU" in sw_version:
        return "PAR-CT01MAU"
    if sw_version and "CT01MA" in sw_version:
        return "PAR-CT01MA"
    return DEVICE_MODEL_ID


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MAConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a climate entity per thermostat subentry, now and as ones are added."""

    @callback
    def _add(subentry_id: str) -> None:
        coordinator = entry.runtime_data.coordinators.get(subentry_id)
        if coordinator is None:
            return
        async_add_entities([MAClimate(coordinator)], config_subentry_id=subentry_id)

    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_TYPE_THERMOSTAT:
            _add(subentry.subentry_id)

    entry.async_on_unload(
        async_dispatcher_connect(hass, f"{SIGNAL_NEW_THERMOSTAT}_{entry.entry_id}", _add)
    )


class MAClimate(CoordinatorEntity[MACoordinator], ClimateEntity):
    """Climate entity for an MA Touch thermostat (pull-model)."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_translation_key = "matouch"

    # The vertical vane is exposed IN the climate card via the SWING_MODE control and the
    # horizontal (left/right) vane via the native SWING_HORIZONTAL_MODE control (HA >=
    # 2024.12) — each added dynamically once caps confirm the unit has that vane (see
    # supported_features / swing_modes / swing_horizontal_modes). Display labels + per-
    # position icons come from the entity translations and icons.json, so the option values
    # are stable machine keys. Hold stays a dedicated switch: it's an on/off, and cramming a
    # toggle into climate's "preset" slot is semantically wrong.
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Base features, plus SWING_MODE (vertical vane) and/or SWING_HORIZONTAL_MODE
        (left/right vane) once caps confirm the unit has each. Dynamic so a unit without a
        given vane never shows an empty control."""
        features = self._attr_supported_features
        caps = self._caps
        if caps is not None:
            if caps.supports_swing:
                features |= ClimateEntityFeature.SWING_MODE
            if caps.supports_right_left:
                features |= ClimateEntityFeature.SWING_HORIZONTAL_MODE
        return features

    def __init__(self, coordinator: MACoordinator) -> None:
        """Initialize the MA Touch climate entity."""

        super().__init__(coordinator)
        mac = coordinator.mac_address
        self._attr_unique_id = f"matouch_{format_mac(mac)}"
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, format_mac(mac))},
            name=coordinator.device_name,
            manufacturer=MANUFACTURER,
            model=DEVICE_MODEL,
            model_id=_model_id_for(coordinator.software_version),
            sw_version=coordinator.software_version,
            hw_version=coordinator.firmware_version,
        )

    async def async_added_to_hass(self) -> None:
        """Back-fill device versions once the device entry exists."""

        await super().async_added_to_hass()
        self._refresh_device_versions()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Write state and back-fill versions if they only just became available."""

        self._refresh_device_versions()
        super()._handle_coordinator_update()

    @callback
    def _refresh_device_versions(self) -> None:
        """Update the device registry sw/hw version after a late first connect.

        device_info is captured once at entity init; for a unit that was offline
        at startup the versions are None then and never re-published. This pushes
        them as soon as a connect reads them.
        """

        sw = self.coordinator.software_version
        hw = self.coordinator.firmware_version
        if sw is None and hw is None:
            return
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(
            connections={(CONNECTION_BLUETOOTH, format_mac(self.coordinator.mac_address))}
        )
        if device is None:
            return
        # Correct the technical model id per unit (Theater is a CT01MA, the rest are
        # CT01MAU) once the software version is known.
        mid = _model_id_for(sw)
        if device.sw_version != sw or device.hw_version != hw or device.model_id != mid:
            registry.async_update_device(device.id, sw_version=sw, hw_version=hw, model_id=mid)

    @property
    def available(self) -> bool:
        """Tolerant availability (shared with the Hold switch): steady through the
        endemic ~43-min BLE reconnect, greys only on a sustained outage. Logic lives on
        the coordinator (card_available) so every control entity agrees."""

        return self.coordinator.card_available

    # --- capability gating ---------------------------------------------------
    # Until the device-info blob is fetched (coordinator.capabilities is None) the
    # entity keeps the full, ungated mode lists so nothing is hidden prematurely; once
    # caps arrive these narrow to exactly what the unit supports.

    @property
    def _caps(self):
        """Parsed per-unit capabilities, or None until first fetched."""
        return self.coordinator.capabilities

    @property
    def hvac_modes(self) -> list[HVACMode]:
        caps = self._caps
        if caps is None:
            return list(HA_TO_MA_HVAC.keys())
        modes = [HVACMode.OFF]
        flags = caps.hvac_modes()
        for name, ha_mode in (
            ("heat", HVACMode.HEAT), ("cool", HVACMode.COOL), ("auto", HVACMode.AUTO),
            ("dry", HVACMode.DRY), ("fan_only", HVACMode.FAN_ONLY),
        ):
            if flags.get(name):
                modes.append(ha_mode)
        return modes

    @property
    def fan_modes(self) -> list[str]:
        caps = self._caps
        if caps is None:
            return list(HA_TO_MA_FAN.keys())
        return caps.fan_modes()

    @property
    def swing_modes(self) -> list[str] | None:
        """Vertical vane position keys (auto / flat / down_20..down_100 / swing), or None
        until caps load or on a unit with no vane. Paired with SWING_MODE in
        supported_features, so the control only appears on vane-capable units. Display
        labels + per-position icons come from the entity translations / icons.json."""
        caps = self._caps
        if caps is None or not caps.supports_swing:
            return None
        return caps.vane_modes()

    @property
    def swing_horizontal_modes(self) -> list[str] | None:
        """Horizontal (left/right) vane position keys, or None until caps load or on a unit
        with no horizontal vane. Paired with SWING_HORIZONTAL_MODE in supported_features."""
        caps = self._caps
        if caps is None or not caps.supports_right_left:
            return None
        return caps.right_left_modes()

    # --- unit handling -------------------------------------------------------
    # The device is Celsius-native (0.5°C). When HA's unit system is Fahrenheit we
    # present °F NATIVELY using Mitsubishi's lookup table so HA performs no generic
    # conversion (which would land on X.5°F and round, disagreeing with the physical
    # controller by ~1°F). In °C systems everything passes through unchanged.

    @property
    def _fahrenheit(self) -> bool:
        return self.hass.config.units.temperature_unit == UnitOfTemperature.FAHRENHEIT

    @property
    def temperature_unit(self) -> str:
        return UnitOfTemperature.FAHRENHEIT if self._fahrenheit else UnitOfTemperature.CELSIUS

    @property
    def precision(self) -> float:
        return PRECISION_WHOLE if self._fahrenheit else PRECISION_HALVES

    @property
    def target_temperature_step(self) -> float:
        # Whole °F (matches the controller's °F mode) / 0.5 °C natively.
        return PRECISION_WHOLE if self._fahrenheit else PRECISION_HALVES

    def _disp_setpoint(self, celsius: float | None) -> float | None:
        """Celsius setpoint -> displayed unit (Mitsubishi table when °F). None-safe:
        a device 'not set' (0xFFFF) setpoint decodes to None and stays None."""
        return to_display_setpoint(celsius, self._fahrenheit) if celsius is not None else None

    def _disp_room(self, celsius: float) -> float:
        """Celsius room temp -> displayed unit (plain rounding when °F)."""
        return to_display_room(celsius, self._fahrenheit)

    def _to_celsius(self, temperature: float) -> float:
        """Displayed-unit setpoint -> Celsius for the device (inverse table)."""
        return from_display_setpoint(temperature, self._fahrenheit)

    # --- pull-model state: read straight from coordinator.data (the Status) ---

    @property
    def _status(self):
        return self.coordinator.data

    @property
    def hvac_mode(self) -> HVACMode | None:
        status = self._status
        return MA_TO_HA_HVAC.get(status.operation_mode) if status else None

    @property
    def current_temperature(self) -> float | None:
        status = self._status
        if status is None or status.room_temperature is None:
            return None
        return self._disp_room(status.room_temperature)

    @property
    def target_temperature(self) -> float | None:
        status = self._status
        if status is None:
            return None
        match status.operation_mode:
            case MAOperationMode.HEAT:
                return self._disp_setpoint(status.heat_setpoint)
            case MAOperationMode.COOL | MAOperationMode.DRY:
                return self._disp_setpoint(status.cool_setpoint)
            case _:
                return None

    @property
    def target_temperature_high(self) -> float | None:
        status = self._status
        if status and status.operation_mode is MAOperationMode.AUTO:
            return self._disp_setpoint(status.cool_setpoint)
        return None

    @property
    def target_temperature_low(self) -> float | None:
        status = self._status
        if status and status.operation_mode is MAOperationMode.AUTO:
            return self._disp_setpoint(status.heat_setpoint)
        return None

    @property
    def min_temp(self) -> float:
        status = self._status
        if status is None:
            return self._disp_setpoint(MA_MIN_TEMP)
        match status.operation_mode:
            case MAOperationMode.HEAT:
                celsius = status.min_heat_temperature
            case MAOperationMode.COOL | MAOperationMode.DRY:
                celsius = status.min_cool_temperature
            case MAOperationMode.AUTO:
                celsius = status.min_auto_temperature
            case _:
                celsius = MA_MIN_TEMP
        if celsius is None:  # device sent 0xFFFF for this bound; fall back to default
            celsius = MA_MIN_TEMP
        return self._disp_setpoint(celsius)

    @property
    def max_temp(self) -> float:
        status = self._status
        if status is None:
            return self._disp_setpoint(MA_MAX_TEMP)
        match status.operation_mode:
            case MAOperationMode.HEAT:
                celsius = status.max_heat_temperature
            case MAOperationMode.COOL | MAOperationMode.DRY:
                celsius = status.max_cool_temperature
            case MAOperationMode.AUTO:
                celsius = status.max_auto_temperature
            case _:
                celsius = MA_MAX_TEMP
        if celsius is None:  # device sent 0xFFFF for this bound; fall back to default
            celsius = MA_MAX_TEMP
        return self._disp_setpoint(celsius)

    @property
    def fan_mode(self) -> str | None:
        status = self._status
        return MA_TO_HA_FAN.get(status.fan_mode) if status else None

    @property
    def swing_mode(self) -> str | None:
        # Mapped by wire VALUE (not enum) to dodge the MAVaneMode NONE/STEP_5 == 0 alias.
        status = self._status
        if status is None:
            return None
        return MA_VANE_VALUE_TO_HA.get(int(status.vane_mode))

    @property
    def swing_horizontal_mode(self) -> str | None:
        """Current horizontal (left/right) vane position, READ from the status frame."""
        status = self._status
        if status is None:
            return None
        return MA_RL_VALUE_TO_HA.get(status.right_left)

    @property
    def hvac_action(self) -> HVACAction | None:
        """What the unit is physically doing right now, READ from the device's running
        state (unit_state) — not inferred from room-vs-setpoint. The running state carries
        real DEFROST and heat pre-warm (PREHEATING), and distinguishes actively-conditioning
        from thermo-satisfied / waiting-for-the-shared-outdoor-unit — none of which inference
        can see. HA permits only the eight HVACAction members, so states with no exact
        equivalent (NORMAL / WAIT_MULTI / REQUEST_COMP_OFF) collapse to IDLE; the raw running
        state is preserved in extra_state_attributes for automations/history."""
        status = self._status
        if status is None:
            return None
        mode = status.operation_mode
        if mode is MAOperationMode.OFF:
            return HVACAction.OFF
        if mode is MAOperationMode.FAN:
            # Fan-only always moves air; the compressor never serves this head.
            return HVACAction.FAN
        action = MA_UNIT_STATE_TO_HVAC_ACTION.get(status.unit_state)
        if action is None:
            # NORMAL / WAIT_MULTI / REQUEST_COMP_OFF / unknown -> not actively conditioning.
            return HVACAction.IDLE
        # In DRY mode the compressor runs a cooling cycle to dehumidify; label it DRYING.
        if mode is MAOperationMode.DRY and action is HVACAction.COOLING:
            return HVACAction.DRYING
        return action

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Expose the raw device running state so the nuance hvac_action's IDLE collapses
        (setpoint-satisfied vs waiting-for-outdoor-unit vs defrost) stays queryable in
        automations and recorded in history."""
        status = self._status
        if status is None:
            return None
        return {
            "unit_state": MA_UNIT_STATE_NAMES.get(
                status.unit_state, f"unknown_{status.unit_state}"
            )
        }

    # --- commands ---

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""

        status = self.coordinator.data
        if status is None:
            raise ServiceValidationError("Thermostat state not yet available")
        try:
            # Incoming values are in the entity's display unit; map back to the
            # device's 0.5°C grid (inverse Mitsubishi table) before sending.
            if (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
                temperature = self._to_celsius(temperature)
                match status.operation_mode:
                    case MAOperationMode.HEAT:
                        await self.coordinator.async_set_heat_setpoint(temperature)
                    case MAOperationMode.COOL | MAOperationMode.DRY:
                        await self.coordinator.async_set_cool_setpoint(temperature)
                    case _:
                        raise ServiceValidationError("Target setpoint is ambiguous in this mode")
            if (temperature := kwargs.get(ATTR_TARGET_TEMP_LOW)) is not None:
                await self.coordinator.async_set_heat_setpoint(self._to_celsius(temperature))
            if (temperature := kwargs.get(ATTR_TARGET_TEMP_HIGH)) is not None:
                await self.coordinator.async_set_cool_setpoint(self._to_celsius(temperature))
        except MAException as ex:
            raise ServiceValidationError(f"Failed to set temperature: {ex}") from ex

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target HVAC mode."""

        try:
            await self.coordinator.async_set_operation_mode(HA_TO_MA_HVAC[hvac_mode])
        except MAException as ex:
            raise ServiceValidationError(f"Failed to set HVAC mode: {ex}") from ex

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set new target fan mode."""

        try:
            await self.coordinator.async_set_fan_mode(HA_TO_MA_FAN[fan_mode])
        except MAException as ex:
            raise ServiceValidationError(f"Failed to set fan mode: {ex}") from ex

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set the vane (vertical airflow) position via the climate swing control."""

        vane_mode = HA_TO_MA_VANE.get(swing_mode)
        if vane_mode is None:
            raise ServiceValidationError(f"Unknown vane position: {swing_mode}")
        try:
            await self.coordinator.async_set_vane_mode(vane_mode)
        except MAException as ex:
            raise ServiceValidationError(f"Failed to set vane: {ex}") from ex

    async def async_set_swing_horizontal_mode(self, swing_horizontal_mode: str) -> None:
        """Set the horizontal (left/right) vane position via the climate control."""

        right_left = HA_TO_MA_RL.get(swing_horizontal_mode)
        if right_left is None:
            raise ServiceValidationError(
                f"Unknown horizontal vane position: {swing_horizontal_mode}"
            )
        try:
            await self.coordinator.async_set_right_left_mode(right_left)
        except MAException as ex:
            raise ServiceValidationError(f"Failed to set horizontal vane: {ex}") from ex
