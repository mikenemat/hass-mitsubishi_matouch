"""Models for the btmatouch library."""

from __future__ import annotations
from typing import Self

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ._structures import (
    _MAStruct,
    _MAStatusResponse,
)
from .const import (
    MAOperationMode,
    _MAOperationModeFlags,
    MAFanMode,
    MAVaneMode,
)

__all__ = [
    "Status",
]


@dataclass
class _BaseModel[StructType: _MAStruct](ABC):
    @classmethod
    @abstractmethod
    def _from_struct(cls: type[Self], struct: StructType) -> Self:
        """Convert the structure to a model."""

    @classmethod
    @abstractmethod
    def _struct_type(cls: type[Self]) -> type[StructType]:
        """Return the structure type associated with the model."""

    @classmethod
    def _from_bytes(cls: type[Self], data: bytes) -> Self:
        """Convert the data to a model."""
        return cls._from_struct(cls._struct_type().from_bytes(data))


@dataclass
class Status(_BaseModel[_MAStatusResponse]):
    """Status model."""

    # Temperatures may be None: the device sends 0xFFFF ("not set") for setpoints/limits
    # a given unit or mode doesn't use (see _MATemperature.decode). Consumers must guard.
    max_cool_temperature: float | None
    min_cool_temperature: float | None
    max_heat_temperature: float | None
    min_heat_temperature: float | None
    max_auto_temperature: float | None
    min_auto_temperature: float | None
    cool_setpoint: float | None
    heat_setpoint: float | None
    room_temperature: float | None
    fan_mode: MAFanMode
    vane_mode: MAVaneMode
    hold: bool
    operation_mode: MAOperationMode
    # Running state + horizontal-vane position, decoded from the status frame's
    # running-state byte (struct.unknown_8). unit_state is the low nibble (heat/cool/
    # defrost/standby/etc. — see const.MA_UNIT_STATE_*); right_left is bits 4-6 (the L/R
    # vane position, MARightLeftMode). Raw ints so an unmapped/reserved wire value never
    # raises. Defaults keep older construction/replace() call sites working.
    unit_state: int = 0
    right_left: int = 0

    @classmethod
    def _from_struct(cls, struct: _MAStatusResponse) -> Self:
        return cls(
            max_cool_temperature=struct.max_cool_temperature,
            min_cool_temperature=struct.min_cool_temperature,
            max_heat_temperature=struct.max_heat_temperature,
            min_heat_temperature=struct.min_heat_temperature,
            max_auto_temperature=struct.max_auto_temperature,
            min_auto_temperature=struct.min_auto_temperature,
            cool_setpoint=struct.cool_setpoint,
            heat_setpoint=struct.heat_setpoint,
            room_temperature=struct.room_temperature,
            fan_mode=struct.fan_mode,
            vane_mode=struct.vane_mode,
            hold=struct.hold,
            # unit_state = low nibble, right_left = bits 4-6 (first-declared field lands in
            # the low bits per the SDK bit packer — verified against sdk/a.java).
            unit_state=struct.unknown_8 & 0x0F,
            right_left=(struct.unknown_8 >> 4) & 0x07,
            operation_mode=MAOperationMode.AUTO if struct.operation_mode_flags & (_MAOperationModeFlags.FAN|_MAOperationModeFlags.AUTO) == (_MAOperationModeFlags.FAN|_MAOperationModeFlags.AUTO)
            else MAOperationMode.DRY if struct.operation_mode_flags & (_MAOperationModeFlags.FAN|_MAOperationModeFlags.DRY|_MAOperationModeFlags.HEAT) == (_MAOperationModeFlags.FAN|_MAOperationModeFlags.DRY|_MAOperationModeFlags.HEAT)
            else MAOperationMode.HEAT if struct.operation_mode_flags & (_MAOperationModeFlags.FAN|_MAOperationModeFlags.HEAT) == (_MAOperationModeFlags.FAN|_MAOperationModeFlags.HEAT)
            else MAOperationMode.COOL if struct.operation_mode_flags & (_MAOperationModeFlags.FAN|_MAOperationModeFlags.COOL) == (_MAOperationModeFlags.FAN|_MAOperationModeFlags.COOL)
            else MAOperationMode.FAN if struct.operation_mode_flags & _MAOperationModeFlags.FAN
            else MAOperationMode.OFF,
        )

    @classmethod
    def _struct_type(cls) -> type[_MAStatusResponse]:
        return _MAStatusResponse
