"""Constants for the Mitsubishi MA Touch integration."""

from homeassistant.components.climate import HVACMode
from homeassistant.components.climate.const import (
    FAN_AUTO,
    FAN_HIGH,
    FAN_LOW,
    FAN_MEDIUM,
    FAN_OFF,
)

from .btmatouch.const import MAOperationMode, MAFanMode, MAVaneMode
from .btmatouch.capabilities import VANE_AUTO, VANE_SWING, VANE_POSITION_LABELS

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

# --- Vane (vertical airflow) <-> HA swing-mode labels --------------------------
# The vane has up to 5 fixed positions plus auto + swing, surfaced via the climate swing
# control (gated by the per-unit vane capability). Labels are the human-readable airflow
# directions (capabilities.VANE_POSITION_LABELS), AUTHORITATIVE from the decompiled SDK:
# wire 3 = FLAT (horizontal), 5 = DOWNWARD20, 2 = DOWNWARD60, 1 = DOWNWARD80,
# 0 = DOWNWARD100 (fully down). (GeminiMobileData WindDirection.toRequestValue /
# convertDirectionToVane.)
#
# READ is mapped by the WIRE VALUE (not the MAVaneMode member) because MAVaneMode.NONE
# and STEP_5 both equal 0 — an enum lookup of 0 yields NONE, so a value table avoids
# that alias. SET maps the HA label to the right MAVaneMode member.
MA_VANE_VALUE_TO_HA: dict[int, str] = {
    int(MAVaneMode.AUTO): VANE_AUTO,                 # 6
    int(MAVaneMode.STEP_1): VANE_POSITION_LABELS[0], # 3 -> horizontal
    int(MAVaneMode.STEP_2): VANE_POSITION_LABELS[1], # 5 -> down 20%
    int(MAVaneMode.STEP_3): VANE_POSITION_LABELS[2], # 2 -> down 60%
    int(MAVaneMode.STEP_4): VANE_POSITION_LABELS[3], # 1 -> down 80%
    int(MAVaneMode.STEP_5): VANE_POSITION_LABELS[4], # 0 -> down 100%
    int(MAVaneMode.SWING): VANE_SWING,               # 7
}
HA_TO_MA_VANE: dict[str, MAVaneMode] = {
    VANE_AUTO: MAVaneMode.AUTO,
    VANE_POSITION_LABELS[0]: MAVaneMode.STEP_1,
    VANE_POSITION_LABELS[1]: MAVaneMode.STEP_2,
    VANE_POSITION_LABELS[2]: MAVaneMode.STEP_3,
    VANE_POSITION_LABELS[3]: MAVaneMode.STEP_4,
    VANE_POSITION_LABELS[4]: MAVaneMode.STEP_5,
    VANE_SWING: MAVaneMode.SWING,
}

# HOLD is surfaced as a climate preset (only on units whose capability blob advertises
# holdfunc). PRESET_NONE is HA's "no preset".
PRESET_HOLD = "hold"

DEFAULT_SCAN_INTERVAL = 10 # seconds; cheap now that the connection is persistent (keepalive holds it)

# When balancing across proxies, skip proxies that hear the device weaker than
# this (dBm), unless no stronger proxy can reach it.
PROXY_RSSI_FLOOR = -90

# Integration-wide option: prefer ESP32 Bluetooth proxies over the HOST's built-in /
# HCI Bluetooth radio for connections, REGARDLESS of RSSI — i.e. only fall back to the
# local adapter when no proxy can reach the device at all. Default on: the host radio
# is oversubscribed by many persistent connections, can't be near every unit, and is
# subject to WiFi/BT coexistence + USB3 interference + kernel-driver breakage — so a
# proxy is the better path in essentially every scenario except outright unavailability.
CONF_PREFER_PROXY = "prefer_proxy"
DEFAULT_PREFER_PROXY = True

# Dispatcher signal (namespaced per entry) fired when a thermostat subentry is
# added at runtime so the platforms create its entities without a parent reload.
SIGNAL_NEW_THERMOSTAT = "mitsubishi_matouch_new_thermostat"

# Dispatcher signal (namespaced per entry, payload = subentry_id) fired once when a
# unit's capability blob first loads. Capability-gated entities that only make sense on
# units with the feature (the Vane select, the Hold switch) are created in response, so
# they appear only where supported rather than as permanently-unavailable phantoms.
SIGNAL_CAPS_LOADED = "mitsubishi_matouch_caps_loaded"

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

# How long the device must keep answering operation/settings requests with
# ERROR_FROM_DEVICE (result 0x09) before we raise the "thermostat fault" Repairs notice.
# The device is connected and authenticates (so this isn't a link/wedge issue) but
# rejects everything — a unit stuck on an error/startup screen. Long enough that a user
# briefly in the on-device menus (which also returns 0x09) doesn't trip it; it clears the
# moment a poll succeeds.
DEVICE_FAULT_THRESHOLD = 180
