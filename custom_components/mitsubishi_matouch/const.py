"""Constants for the Mitsubishi MA Touch integration."""

from homeassistant.components.climate import HVACMode
from homeassistant.components.climate.const import (
    FAN_AUTO,
    FAN_HIGH,
    FAN_LOW,
    FAN_MEDIUM,
    FAN_OFF,
)

from .btmatouch.const import MAOperationMode, MAFanMode

DOMAIN = "mitsubishi_matouch"

SUBENTRY_TYPE_THERMOSTAT = "thermostat"
# Units advertise as "M/R_CT01MAU_<machex>" (and "M/R_CT01MA_<machex>" on some),
# so match the "CT01MA" model designation as a substring, not a strict prefix.
MA_TOUCH_NAME_MATCH = "CT01MA"
# Note: this is a GATT service, NOT advertised — kept only as a secondary hint.
MA_TOUCH_SERVICE_UUID = "0277df18-e796-11e6-bf01-fe55135034f3"

MANUFACTURER = "Mitsubishi Electric"
DEVICE_MODEL = "MA Touch"
DEVICE_MODEL_ID = "PAR-CT01MAU"

MA_TO_HA_HVAC: dict[MAOperationMode, HVACMode] = {
    MAOperationMode.OFF: HVACMode.OFF,
    MAOperationMode.AUTO: HVACMode.AUTO,
    MAOperationMode.HEAT: HVACMode.HEAT,
    MAOperationMode.COOL: HVACMode.COOL,
    MAOperationMode.DRY: HVACMode.DRY,
    MAOperationMode.FAN: HVACMode.FAN_ONLY,
}

HA_TO_MA_HVAC: dict[HVACMode, MAOperationMode] = {
    HVACMode.OFF: MAOperationMode.OFF,
    HVACMode.AUTO: MAOperationMode.AUTO,
    HVACMode.HEAT: MAOperationMode.HEAT,
    HVACMode.COOL: MAOperationMode.COOL,
    HVACMode.DRY: MAOperationMode.DRY,
    HVACMode.FAN_ONLY: MAOperationMode.FAN,
}

MA_TO_HA_FAN: dict[MAFanMode, str] = {
    MAFanMode.AUTO: FAN_AUTO,
    MAFanMode.HIGH: FAN_HIGH,
    MAFanMode.MEDIUM: FAN_MEDIUM,
    MAFanMode.LOW: FAN_LOW,
    MAFanMode.QUIET: "quiet",
}

HA_TO_MA_FAN: dict[str, MAFanMode] = {
    FAN_AUTO: MAFanMode.AUTO,
    FAN_HIGH: MAFanMode.HIGH,
    FAN_MEDIUM: MAFanMode.MEDIUM,
    FAN_LOW: MAFanMode.LOW,
    "quiet": MAFanMode.QUIET,
}

DEFAULT_SCAN_INTERVAL = 10 # seconds; cheap now that the connection is persistent (keepalive holds it)

# When balancing across proxies, skip proxies that hear the device weaker than
# this (dBm), unless no stronger proxy can reach it.
PROXY_RSSI_FLOOR = -90

# Dispatcher signal (namespaced per entry) fired when a thermostat subentry is
# added at runtime so the platforms create its entities without a parent reload.
SIGNAL_NEW_THERMOSTAT = "mitsubishi_matouch_new_thermostat"

# Active-rebalance tuning. Rebalance is event-driven (proxy online/offline) with a
# slow periodic backstop; a debouncer coalesces bursts and serializes runs.
REBALANCE_INTERVAL = 600       # slow periodic backstop (seconds)
REBALANCE_COOLDOWN = 1800      # min seconds before re-bouncing the same device
REBALANCE_DEBOUNCE = 5.0       # coalesce a burst of proxy events into one run
REBALANCE_STEP_DELAY = 6.0     # let a bounced device reconnect before re-evaluating

# Per-coordinator exponential backoff cap (seconds) on repeated connect/link
# failures, so an unreachable unit / saturated proxy is not hammered every poll.
MAX_BACKOFF_INTERVAL = 120

# How often to re-evaluate a failing unit's availability. HA's DataUpdateCoordinator
# only fires entity updates on a success or the FIRST failure of a streak, so a
# time-based "stale" grey-out would otherwise never be re-checked during a sustained
# outage (the card stays online showing stale data). A timer nudges listeners while a
# unit is failing so it greys on schedule. The same tick re-evaluates the
# "wedged" (discoverable-but-won't-connect) Repairs notice below.
AVAILABILITY_TICK_INTERVAL = 15

# How long a unit may stay DISCOVERABLE (a proxy sees it advertising) while EVERY
# connect/poll fails before we raise a Repairs notice that its radio is likely wedged
# and needs a power cycle. Set well above the normal periodic drop-and-reconnect blip
# (and above the exponential backoff) so an ordinary recoverable hiccup never trips it;
# only a genuinely stuck unit (advertising yet unjoinable for this long) does.
WEDGED_UNIT_THRESHOLD = 600
