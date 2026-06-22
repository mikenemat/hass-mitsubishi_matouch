"""Pure-function tests for the Mitsubishi °F/°C mapping (no Home Assistant deps).

The device is Celsius-native; °F is a non-linear controller display transform that
this integration reproduces so the HA card matches the physical controller. These
tests pin the table, the round-trip stability the write path relies on, and — for
interoperability — that Celsius systems get an exact passthrough.

    python tests/test_temperature.py      # standalone
    pytest tests/test_temperature.py      # or via pytest
"""

import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_MOD = os.path.join(_HERE, "..", "custom_components", "mitsubishi_matouch", "temperature.py")
_spec = importlib.util.spec_from_file_location("ma_temperature", _MOD)
t = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(t)

# Mitsubishi "standard" table — echavet/MitsubishiCN105ESPHome localization.h.
STANDARD = {
    61: 16.0, 62: 16.5, 63: 17.0, 64: 17.5, 65: 18.0, 66: 18.5, 67: 19.0,
    68: 20.0, 69: 21.0, 70: 21.5, 71: 22.0, 72: 22.5, 73: 23.0, 74: 23.5,
    75: 24.0, 76: 24.5, 77: 25.0, 78: 25.5, 79: 26.0, 80: 26.5, 81: 27.0,
    82: 27.5, 83: 28.0, 84: 28.5, 85: 29.0, 86: 29.5, 87: 30.0, 88: 30.5,
}


def test_table_fidelity():
    for f, c in STANDARD.items():
        assert t.setpoint_f_to_c(f) == c, f
        assert t.setpoint_c_to_f(c) == f, c


def test_measured_ground_truth():
    # Verified live on this site's units.
    assert t.setpoint_c_to_f(22.0) == 71
    assert t.setpoint_c_to_f(22.5) == 72
    assert t.setpoint_f_to_c(71) == 22.0
    assert t.setpoint_f_to_c(72) == 22.5


def test_roundtrip_fahrenheit():
    for f in STANDARD:
        assert t.setpoint_c_to_f(t.setpoint_f_to_c(f)) == f, f


def test_roundtrip_celsius():
    for c in STANDARD.values():
        assert t.setpoint_f_to_c(t.setpoint_c_to_f(c)) == c, c


def test_double_steps():
    # 67->68 (19.0->20.0) and 68->69 (20.0->21.0) each jump a full 1.0°C.
    assert t.setpoint_f_to_c(68) - t.setpoint_f_to_c(67) == 1.0
    assert t.setpoint_f_to_c(69) - t.setpoint_f_to_c(68) == 1.0


def test_room_is_plain_halfup_not_table():
    assert t.room_c_to_f(22.0) == 72.0
    assert t.room_c_to_f(22.5) == 73.0  # plain half-up, NOT the table's 72


def test_celsius_passthrough_is_identity():
    # The interoperability guarantee: in °C mode nothing is transformed.
    for c in (16.0, 19.5, 20.5, 22.0, 22.5, 30.5, 31.0):
        assert t.to_display_setpoint(c, False) == c
        assert t.to_display_room(c, False) == c
        assert t.from_display_setpoint(c, False) == c


def test_fahrenheit_wrappers():
    assert t.to_display_setpoint(22.5, True) == 72
    assert t.from_display_setpoint(72, True) == 22.5
    assert t.to_display_room(22.5, True) == 73.0


def test_edges_do_not_crash():
    assert t.setpoint_c_to_f(31.0) == 88        # above table top -> plain
    assert t.setpoint_c_to_f(10.0) == 50        # below table -> plain
    assert t.setpoint_f_to_c(50) == 16.0        # below range -> clamp
    assert t.setpoint_f_to_c(99) == 30.5        # above range -> clamp
    assert t.setpoint_f_to_c(72.5) == 22.5      # fractional °F -> rounds


if __name__ == "__main__":
    import sys
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as ex:
            failed += 1
            print(f"FAIL {fn.__name__}: {ex!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
