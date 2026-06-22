"""Mitsubishi Fahrenheit display mapping.

The MA Touch family is **Celsius-native**: every temperature on the wire is °C in
0.5° steps, and the controller's °F display is a non-linear lookup transform applied
locally (it is NOT the F = C*9/5+32 formula). If we report °C and let Home Assistant
do the generic conversion, a 0.5°C setpoint lands on an X.5°F value that the frontend
rounds — so the HA card disagrees with the physical controller by ~1°F (upstream
issue #4).

To match the controller exactly, the climate entity presents Fahrenheit natively
(when HA's unit system is °F) using Mitsubishi's own table, so HA performs no
conversion. Setpoints use the discrete table; room/current temperature uses ordinary
rounding (the controller treats them differently).

Table = Mitsubishi "standard" firmware variant, verified against this site's units
(71°F→22.0°C, 72°F→22.5°C). The double-steps at 67→68→69°F (each jumps a full 1.0°C)
are what make it non-linear. Source: echavet/MitsubishiCN105ESPHome localization.h.
A second "alt" variant exists (71°F→21.5°C) but does NOT describe these units.
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


def room_c_to_f(celsius: float) -> float:
    """Convert a room/current temperature to °F (ordinary half-up rounding, NOT the
    setpoint table). Room temp may therefore differ by 1°F from an equal setpoint —
    that's expected; the controller renders the sensor reading the same way.

    Half-up (not Python's banker's round) so 72.5°F -> 73, predictably; room temps
    are always positive so floor(x + 0.5) is correct.
    """

    return float(int(celsius * 9 / 5 + 32 + 0.5))


# --- unit-aware wrappers (interoperable: °C systems get an exact passthrough) ---
# These centralize the "Fahrenheit or not" decision in one tested place. When
# fahrenheit is False the device's native °C value is returned/accepted UNCHANGED,
# i.e. identical to the integration's original Celsius behavior — so a Celsius HA
# instance is unaffected. When True, the Mitsubishi table / room rounding applies.


def to_display_setpoint(celsius: float, fahrenheit: bool) -> float:
    """Setpoint or min/max bound -> display unit (table °F, or °C passthrough)."""

    return setpoint_c_to_f(celsius) if fahrenheit else celsius


def to_display_room(celsius: float, fahrenheit: bool) -> float:
    """Room/current temp -> display unit (plain-rounded °F, or °C passthrough)."""

    return room_c_to_f(celsius) if fahrenheit else celsius


def from_display_setpoint(value: float, fahrenheit: bool) -> float:
    """Display-unit setpoint -> device 0.5°C grid (inverse table, or °C passthrough)."""

    return setpoint_f_to_c(value) if fahrenheit else value
