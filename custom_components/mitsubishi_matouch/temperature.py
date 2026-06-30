"""Mitsubishi Fahrenheit display mapping.

The MA Touch family is **Celsius-native**: every temperature on the wire is °C in
0.5° steps, and the controller's °F display is a non-linear lookup transform applied
locally (it is NOT the F = C*9/5+32 formula). If we report °C and let Home Assistant
do the generic conversion, a 0.5°C setpoint lands on an X.5°F value that the frontend
rounds — so the HA card disagrees with the physical controller by ~1°F (upstream
issue #4).

To match the controller exactly, the climate entity presents Fahrenheit natively
(when HA's unit system is °F) using Mitsubishi's own table, so HA performs no
conversion. **Setpoint AND room/current temperature use the SAME lookup table** —
confirmed by decompiling the official MELRemo app (v4.2.2), whose single `toFahrenheit`
table drives both `originalTemp` (setpoint) and `status.currentTemp` (room). It is NOT
linear, NOT floor, NOT round — it has nonlinear "double-steps" where two adjacent
0.5°C values collapse to one °F.

The setpoint table (`_F_TO_C`) covers 16.0–30.5°C / 61–88°F and matches MELRemo
exactly. `_ROOM_C_TO_F` below is the full 0.0–40.0°C table (a superset, identical in
the overlap) used for room temp, which can range outside the setpoint band.

Verified against this site's units (71°F→22.0°C, 72°F→22.5°C) and live wall readings.
Source: the MELRemo app + echavet/MitsubishiCN105ESPHome localization.h ("standard"
variant; a second "alt" variant — 71°F→21.5°C — does NOT describe these units).
"""

from __future__ import annotations

# Mitsubishi STANDARD °F -> °C setpoint table (whole °F -> 0.5°C grid).
_F_TO_C: dict[int, float] = {
    61: 16.0, 62: 16.5, 63: 17.0, 64: 17.5, 65: 18.0, 66: 18.5, 67: 19.0,
    68: 20.0, 69: 21.0, 70: 21.5, 71: 22.0, 72: 22.5, 73: 23.0, 74: 23.5,
    75: 24.0, 76: 24.5, 77: 25.0, 78: 25.5, 79: 26.0, 80: 26.5, 81: 27.0,
    82: 27.5, 83: 28.0, 84: 28.5, 85: 29.0, 86: 29.5, 87: 30.0, 88: 30.5,
}
# Inverse (°C -> whole °F). The table is injective on its °C values (19.5 and 20.5
# are skipped by the double-steps), so this is unambiguous.
_C_TO_F: dict[float, int] = {c: f for f, c in _F_TO_C.items()}

_F_MIN = min(_F_TO_C)
_F_MAX = max(_F_TO_C)


def setpoint_c_to_f(celsius: float) -> int:
    """Convert a Celsius setpoint to the whole °F the controller would show."""

    key = round(celsius * 2) / 2  # snap to the 0.5°C grid before lookup
    if key in _C_TO_F:
        return _C_TO_F[key]
    # Out-of-table (e.g. 31.0°C / 10.0°C on some models): plain conversion.
    return round(celsius * 9 / 5 + 32)


def setpoint_f_to_c(fahrenheit: float) -> float:
    """Convert a whole-°F setpoint to the device's 0.5°C grid via the table."""

    key = int(round(fahrenheit))
    if key in _F_TO_C:
        return _F_TO_C[key]
    if key < _F_MIN:
        return _F_TO_C[_F_MIN]
    if key > _F_MAX:
        return _F_TO_C[_F_MAX]
    # Inside the range but not a table key shouldn't happen (table is contiguous);
    # fall back to a 0.5°C-snapped plain conversion just in case.
    return round(((fahrenheit - 32) * 5 / 9) * 2) / 2


# Full Mitsubishi °C->°F display table (0.0–40.0 °C @ 0.5 °C -> whole °F), extracted
# verbatim from the MELRemo app's `celciusToFahrenheit` map. The controller uses this
# SAME table for room/current temp as for setpoints. It is nonlinear: 11 "double-steps"
# (e.g. 19.0 & 19.5 both -> 67, 20.0 & 20.5 both -> 68) where two 0.5 °C values share a
# °F. The 16.0–30.5 °C window equals _F_TO_C exactly (cross-checked in tests).
_ROOM_C_TO_F: dict[float, int] = {
    0.0: 32, 0.5: 33, 1.0: 34, 1.5: 35, 2.0: 36, 2.5: 37, 3.0: 37, 3.5: 38,
    4.0: 39, 4.5: 40, 5.0: 41, 5.5: 42, 6.0: 43, 6.5: 44, 7.0: 45, 7.5: 46,
    8.0: 46, 8.5: 47, 9.0: 48, 9.5: 49, 10.0: 50, 10.5: 51, 11.0: 52, 11.5: 53,
    12.0: 53, 12.5: 54, 13.0: 55, 13.5: 56, 14.0: 57, 14.5: 58, 15.0: 59, 15.5: 60,
    16.0: 61, 16.5: 62, 17.0: 63, 17.5: 64, 18.0: 65, 18.5: 66, 19.0: 67, 19.5: 67,
    20.0: 68, 20.5: 68, 21.0: 69, 21.5: 70, 22.0: 71, 22.5: 72, 23.0: 73, 23.5: 74,
    24.0: 75, 24.5: 76, 25.0: 77, 25.5: 78, 26.0: 79, 26.5: 80, 27.0: 81, 27.5: 82,
    28.0: 83, 28.5: 84, 29.0: 85, 29.5: 86, 30.0: 87, 30.5: 88, 31.0: 88, 31.5: 89,
    32.0: 89, 32.5: 90, 33.0: 91, 33.5: 92, 34.0: 93, 34.5: 94, 35.0: 95, 35.5: 96,
    36.0: 97, 36.5: 98, 37.0: 99, 37.5: 100, 38.0: 100, 38.5: 101, 39.0: 102,
    39.5: 103, 40.0: 104,
}
_ROOM_C_MIN = min(_ROOM_C_TO_F)
_ROOM_C_MAX = max(_ROOM_C_TO_F)


def room_c_to_f(celsius: float) -> int:
    """Convert a room/current temperature to the whole °F the controller displays,
    via the controller's own lookup table (NOT linear, floor, or round).

    Confirmed by the MELRemo app: room temp goes through the same `toFahrenheit` table
    as setpoints. A previous version truncated `int(c*9/5+32)`, which read 1 °F LOW
    below 20 °C and at/above 25.5 °C (it only matched the wall display in the 20–25 °C
    band, where the table happens to equal truncation). The table is correct everywhere.
    """

    key = round(celsius * 2) / 2  # snap to the 0.5 °C grid the device reports on
    value = _ROOM_C_TO_F.get(key)
    if value is not None:
        return value
    # Outside 0–40 °C (implausible for an indoor sensor): clamp to the table ends.
    if key < _ROOM_C_MIN:
        return _ROOM_C_TO_F[_ROOM_C_MIN]
    return _ROOM_C_TO_F[_ROOM_C_MAX]


# --- unit-aware wrappers (interoperable: °C systems get an exact passthrough) ---
# These centralize the "Fahrenheit or not" decision in one tested place. When
# fahrenheit is False the device's native °C value is returned/accepted UNCHANGED,
# i.e. identical to the integration's original Celsius behavior — so a Celsius HA
# instance is unaffected. When True, the Mitsubishi table / room rounding applies.


def to_display_setpoint(celsius: float, fahrenheit: bool) -> float:
    """Setpoint or min/max bound -> display unit (table °F, or °C passthrough)."""

    return setpoint_c_to_f(celsius) if fahrenheit else celsius


def to_display_room(celsius: float, fahrenheit: bool) -> float:
    """Room/current temp -> display unit.

    °F: truncated to match the controller (see room_c_to_f). °C: EXACT passthrough.
    The device is Celsius-native and reports room temp at the controller's own display
    resolution (0.5°C, observed live), so relaying the value verbatim makes HA match
    the controller with NO conversion — hence no rounding rule that could disagree.
    (The °F discrepancy only arose because °F needs a °C->°F conversion; °C does not,
    so imposing any rounding here would be guessing and could create a 0.5°C gap.)
    """

    return room_c_to_f(celsius) if fahrenheit else celsius


def from_display_setpoint(value: float, fahrenheit: bool) -> float:
    """Display-unit setpoint -> device 0.5°C grid (inverse table, or °C passthrough)."""

    return setpoint_f_to_c(value) if fahrenheit else value
