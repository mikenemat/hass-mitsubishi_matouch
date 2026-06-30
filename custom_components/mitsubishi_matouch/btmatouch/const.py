"""Constants for the btmatouch library."""

from enum import IntEnum, StrEnum, auto

from construct_typed import EnumBase, FlagsEnumBase

__all__ = [
    "MA_MIN_TEMP",
    "MA_MAX_TEMP",
    "DEFAULT_MAX_CONNECT_RETRIES",
    "DEFAULT_COMMAND_TIMEOUT",
    "MAOperationMode",
    "MAFanMode",
    "MAVaneMode",
    "MAVentMode",
    "MARightLeftMode",
    "MAMoveEyeMode",
]


"""The minimum temperature that can be set in degrees Celsius."""
MA_MIN_TEMP = 16.0

"""The maximum temperature that can be set in degrees Celsius."""
MA_MAX_TEMP = 31.0

"""The default maximum connect retries."""
DEFAULT_MAX_CONNECT_RETRIES = 3

"""The default command timeout in seconds."""
DEFAULT_COMMAND_TIMEOUT = 5

"""The default response wait timeout in seconds."""
DEFAULT_RESPONSE_TIMEOUT = 5

"""Keepalive read interval in seconds. Must be < the device's ~16s idle-disconnect."""
DEFAULT_KEEPALIVE_INTERVAL = 10


class MAOperationMode(IntEnum):
    """Operation mode enumeration."""

    OFF = auto()
    AUTO = auto()
    HEAT = auto()
    COOL = auto()
    DRY = auto()
    FAN = auto()


class MAFanMode(EnumBase):
    """Fan mode enumeration."""

    NONE = 0x00
    QUIET = 0 << 4
    LOW = 1 << 4
    MEDIUM = 2 << 4
    HIGH = 3 << 4
    AUTO = 4 << 4


class MAVaneMode(EnumBase):
    """Vane mode enumeration."""

    NONE = 0x00
    AUTO = 6
    STEP_1 = 3
    STEP_2 = 5
    STEP_3 = 2
    STEP_4 = 1
    STEP_5 = 0
    SWING = 7


# --- additional control axes (wire values, from the MELRemo SDK) ---
# These ride in the two trailing bytes of the control frame (see _MAControlRequest):
#   louver_vent      byte = (vent << 4) | louver
#   hold_rl_move_eye byte = (move_eye << 4) | (right_left << 1) | hold
# with the change-flag in flags_c (louver 0x04, vent 0x08, hold 0x10,
# right_left 0x20, move_eye 0x40). Support is capability-gated per unit.


class MAVentMode(IntEnum):
    """Ventilation (Lossnay) mode — wire values."""

    OFF = 0
    LOW = 1
    HIGH = 2


class MARightLeftMode(IntEnum):
    """Left/right (horizontal) vane position — wire values."""

    CENTER = 0
    SWING = 1
    AUTO = 2
    LEFT = 3
    LEFT_CENTER = 4
    RIGHT_CENTER = 5
    RIGHT = 6


class MAMoveEyeMode(IntEnum):
    """Move-Eye (i-see occupancy airflow) mode — wire values."""

    OFF = 0
    DIRECT = 1
    INDIRECT = 2
    AREA = 3
    AUTO = 4


class _MACharacteristic(StrEnum):
    """Characteristics enumeration."""
    
    FIRMWARE_VERSION = "799e3b22-e797-11e6-bf01-fe55135034f3"
    SOFTWARE_VERSION = "def9382a-e795-11e6-bf01-fe55135034f3"
    WRITE = "e48c1528-e795-11e6-bf01-fe55135034f3"
    NOTIFY = "ea1ea690-e795-11e6-bf01-fe55135034f3"


class _MAMessageType(EnumBase):
    """Command enumeration."""

    LOGIN_REQUEST = 0x0001 # login
    UNKNOWN_1 = 0x0003 # used during early connection steps, theory: begin session?
    UNKNOWN_2 = 0x0401 # used during early connection steps
    UNKNOWN_3 = 0x0403 # used during teardown steps
    UNKNOWN_4 = 0x0101 # used during teardown steps, theory: logout?
    UNKNOWN_5 = 0x0103 # used during teardown steps, theory: end session?
    STATUS_REQUEST = 0x0205 # request thermostat status
    CONTROL_REQUEST = 0x0105 # control the thermostat


class _MAResult(EnumBase):
    """Result enumeration."""

    SUCCESS = 0x00
    BAD_PIN = 0x02
    IN_MENUS = 0x09
    UNKNOWN_3_BAD_PIN = 0x0a


class _MAOperationModeFlags(FlagsEnumBase):
    """Operation mode flags."""

    NONE = 0x00
    POWER = 1 << 0
    FAN = 1 << 1
    COOL = 1 << 3
    HEAT = 1 << 4
    DRY = 1 << 5
    AUTO = 1 << 6


class _MAOtherFlags(FlagsEnumBase):
    """Other flags."""

    TEMP_RESTRICT = 1 << 2
    POWER = 1 << 4
