"""Mitsubishi Fahrenheit display mapping.

The MA Touch family is **Celsius-native**: every temperature on the wire is °C in
0.5° steps, and the controller's °F display is a non-linear lookup transform applied
locally (it is NOT the F = C*9/5+32 formula). If we report °C and let Home Assistant
do the generic conversion, a 0.5°C setpoint lands on an X.5°F value that the frontend
rounds — so the HA card disagrees with the physical controller by ~1°F (upstream
issue #4).

To match the controller exactly, the climate entity presents Fahrenheit natively
(when HA's unit system is °F) using Mitsubishi's own tables, so HA performs no
conversion.

**Single source of truth (v0.14.9): the official MELRemo app (v4.2.2).** The app
converts EVERY temperature it shows — setpoint AND room/current — through one
`celciusToFahrenheit` table (and its `fahrenheitToCelcius` inverse for input). We use
those two tables verbatim, for both setpoint and room, so we cannot drift from the
controller. The tables are nonlinear: 11 "double-steps" where two adjacent 0.5°C
values collapse to one °F (e.g. 19.0 & 19.5 → 67, 30.5 & 31.0 → 88).

This replaces an earlier hand-transcribed CN105-derived setpoint table
(echavet/MitsubishiCN105ESPHome). That table agreed with MELRemo everywhere EXCEPT
**88°F**, where CN105 mapped to 30.5°C but the controller/MELRemo use **31.0°C** — so
setting max temp commanded a half-degree low. Converging on MELRemo fixes that and
removes the second source. Verified against this site's units (71°F→22.0°C,
72°F→22.5°C) and live wall readings.
"""

from __future__ import annotations

# °C -> °F: MELRemo `celciusToFahrenheit`, verbatim. 0.0–40.0 °C @ 0.5 °C -> whole °F.
# Drives BOTH setpoint and room/current display (the controller uses one table).
_C_TO_F: dict[float, int] = {
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
# °F -> °C: MELRemo `fahrenheitToCelcius`, verbatim. Resolves the double-step
# ambiguity exactly as the controller does on input (e.g. 88°F -> 31.0, not 30.5;
# 67°F -> 19.0). Used only for setpoint INPUT (room temp is read-only).
_F_TO_C: dict[int, float] = {
    32: 0.0, 33: 0.5, 34: 1.0, 35: 1.5, 36: 2.0, 37: 3.0, 38: 3.5, 39: 4.0,
    40: 4.5, 41: 5.0, 42: 5.5, 43: 6.0, 44: 6.5, 45: 7.0, 46: 8.0, 47: 8.5,
    48: 9.0, 49: 9.5, 50: 10.0, 51: 10.5, 52: 11.0, 53: 12.0, 54: 12.5, 55: 13.0,
    56: 13.5, 57: 14.0, 58: 14.5, 59: 15.0, 60: 15.5, 61: 16.0, 62: 16.5, 63: 17.0,
    64: 17.5, 65: 18.0, 66: 18.5, 67: 19.0, 68: 20.0, 69: 21.0, 70: 21.5, 71: 22.0,
    72: 22.5, 73: 23.0, 74: 23.5, 75: 24.0, 76: 24.5, 77: 25.0, 78: 25.5, 79: 26.0,
    80: 26.5, 81: 27.0, 82: 27.5, 83: 28.0, 84: 28.5, 85: 29.0, 86: 29.5, 87: 30.0,
    88: 31.0, 89: 32.0, 90: 32.5, 91: 33.0, 92: 33.5, 93: 34.0, 94: 34.5, 95: 35.0,
    96: 35.5, 97: 36.0, 98: 36.5, 99: 37.0,
}

_C_MIN = min(_C_TO_F)
_C_MAX = max(_C_TO_F)
# MA Touch settable setpoint range (whole °F). Setpoint INPUT is clamped here so an
# out-of-range request can't map to an absurd °C; the per-mode device limits (read
# from the status frame) bound the UI more tightly.
_SETPOINT_F_MIN = 61
_SETPOINT_F_MAX = 88


def c_to_f(celsius: float) -> int:
    """Convert °C to the whole °F the controller displays, via its lookup table.

    The MELRemo app uses this one table for BOTH the setpoint and the room/current
    temperature, so this single function serves both (see the setpoint_c_to_f /
    room_c_to_f aliases). NOT linear, floor, or round — the table is nonlinear.
    """

    key = round(celsius * 2) / 2  # snap to the 0.5 °C grid the device reports on
    value = _C_TO_F.get(key)
    if value is not None:
        return value
    # Outside 0–40 °C (implausible for a setpoint or an indoor sensor): clamp.
    return _C_TO_F[_C_MIN] if key < _C_MIN else _C_TO_F[_C_MAX]


# Setpoint and room °C->°F are the IDENTICAL controller conversion. Kept as named
# aliases for call-site clarity; aliasing makes it impossible for them to drift apart
# again (the old code carried two separate tables — see module docstring).
setpoint_c_to_f = c_to_f
room_c_to_f = c_to_f


def setpoint_f_to_c(fahrenheit: float) -> float:
    """Convert a whole-°F setpoint to the device's °C grid via the controller's
    inverse table (clamped to the settable range)."""

    key = int(round(fahrenheit))
    if key < _SETPOINT_F_MIN:
        key = _SETPOINT_F_MIN
    elif key > _SETPOINT_F_MAX:
        key = _SETPOINT_F_MAX
    return _F_TO_C[key]


# --- unit-aware wrappers (interoperable: °C systems get an exact passthrough) ---
# These centralize the "Fahrenheit or not" decision in one tested place. When
# fahrenheit is False the device's native °C value is returned/accepted UNCHANGED,
# i.e. identical to the integration's original Celsius behavior — so a Celsius HA
# instance is unaffected. When True, the Mitsubishi table applies.


def to_display_setpoint(celsius: float, fahrenheit: bool) -> float:
    """Setpoint or min/max bound -> display unit (table °F, or °C passthrough)."""

    return setpoint_c_to_f(celsius) if fahrenheit else celsius


def to_display_room(celsius: float, fahrenheit: bool) -> float:
    """Room/current temp -> display unit.

    °F: the controller's table (identical to setpoints). °C: EXACT passthrough — the
    device is Celsius-native, so relaying its 0.5°C value verbatim matches the
    controller with no conversion and therefore no rounding rule that could disagree.
    """

    return room_c_to_f(celsius) if fahrenheit else celsius


def from_display_setpoint(value: float, fahrenheit: bool) -> float:
    """Display-unit setpoint -> device °C grid (inverse table, or °C passthrough)."""

    return setpoint_f_to_c(value) if fahrenheit else value
