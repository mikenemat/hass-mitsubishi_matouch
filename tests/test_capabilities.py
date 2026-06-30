"""Capability-blob parser tests, locked against a LIVE device-info response captured
from the Theater CT01MA unit (28:E9:..., fetch_device_info on v0.14.21).

This is ground truth: every decoded value below is cross-checked against a physically
known fact about that unit (two mini-split heads, Fahrenheit display, no hold support,
heat-pump modes, has vanes), so the recursive bit-packing/reversal is validated by
reality, not just by reading the SDK.

    pytest tests/test_capabilities.py
"""

import importlib.util
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_BT = os.path.join(_HERE, "..", "custom_components", "mitsubishi_matouch", "btmatouch")

# capabilities.py is pure-bytes (no construct/bleak), so load it standalone.
_pkg = types.ModuleType("btx")
_pkg.__path__ = [_BT]
sys.modules["btx"] = _pkg
_spec = importlib.util.spec_from_file_location("btx.capabilities", os.path.join(_BT, "capabilities.py"))
caps_mod = importlib.util.module_from_spec(_spec)
sys.modules["btx.capabilities"] = caps_mod
_spec.loader.exec_module(caps_mod)

# Live device-info DATA-frame response from Theater (CT01MA), v0.14.21, 2026-06-30
# (82 bytes = 6-byte response header + 76-byte m0/i/a blob). Reproduced byte-identical
# across repeated fetches. Verbatim from fetch_device_info -> frames.data.
THEATER = bytes.fromhex(
    "050000000000004f0010036001850200011003600100000000000000004b04010000010000010001000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
)


def test_theater_blob_offsets_and_length():
    caps = caps_mod.parse_device_info(THEATER)
    # 82-byte response = 6-byte header + 76-byte m0/i/a (no optional elec/AI byte).
    assert len(THEATER) == 82
    assert caps.electricity is False
    assert caps.ai is False


def test_theater_capabilities_match_known_facts():
    caps = caps_mod.parse_device_info(THEATER)

    # Two mini-split heads at MNET addresses 0 and 1.
    assert caps.num_indoor_units == 2
    assert [u.address for u in caps.indoor_units] == [0, 1]

    # Fahrenheit display (validates the F lookup-table work).
    assert caps.temp_unit == "F"

    # No hold support (validates removing the dead HOLD switch).
    assert caps.hold is False

    # Heat-pump mini-split modes.
    assert caps.cool and caps.heat and caps.dry and caps.fan
    assert caps.auto and caps.auto_kind == 2
    assert caps.setback is False

    # Vertical vanes present; 4 fan speeds + auto.
    assert caps.vane == 4
    assert caps.fan_steps == 4
    assert caps.fan_auto is True

    # These heads have no louver / horizontal vane / move-eye / ventilation.
    assert caps.louver is False
    assert caps.right_left_steps == 0
    assert caps.move_eye is False
    assert caps.lossnai is False

    assert caps.connect_unit == "slim"


def test_reserved_fields_zero_prove_layout():
    """Reserved bits landing on 0 (no bit-bleed from neighbours) corroborates the
    offsets independently of the semantic fields."""
    caps = caps_mod.parse_device_info(THEATER)
    assert caps.raw["top"]["reserved01"] == 0
    assert caps.raw["top"]["reserved02"] == 0
    assert caps.raw["top"]["reserved03"] == 0
    assert caps.raw["modes"]["reserved"] == 0
    assert caps.raw["fu_func"]["reserved"] == 0


def test_as_dict_is_json_friendly():
    d = caps_mod.parse_device_info(THEATER).as_dict()
    assert d["num_indoor_units"] == 2
    assert d["temp_unit"] == "F"
    assert d["modes"]["heat"] is True
    assert len(d["indoor_units"]) == 2


def test_short_blob_raises():
    import pytest

    with pytest.raises(ValueError):
        caps_mod.parse_device_info(b"\x05\x00\x00\x00\x00\x00")


# More live blobs for cross-validation across the fleet (all CT01MAU, fetched 2026-06-30):
#  - 2D:15: 1 indoor unit, vane=0 (NO swing), 3 fan steps (NO quiet), hold supported.
#  - 40:87: 2 indoor units, vane=4 (swing), 4 fan steps (quiet), hold supported.
MAU_2D15 = bytes.fromhex(
    "050000000000002f0000039001800270018002900100039001800270010aa4000000010000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
)
MAU_4087 = bytes.fromhex(
    "050000000000002f0000039001800270018002900100039001800270014ba4010000010000010001000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
)


def test_ct01mau_2d15_profile_and_gating():
    caps = caps_mod.parse_device_info(MAU_2D15)
    assert caps.num_indoor_units == 1
    assert caps.temp_unit == "F"
    assert caps.hold is True            # CT01MAU supports hold (Theater/CT01MA does not)
    assert caps.vane == 0
    assert caps.supports_swing is False
    assert caps.fan_steps == 3
    # 3-step unit: NO 'quiet' offered (this is the speed-revert wart fix).
    assert caps.fan_modes() == ["low", "medium", "high", "auto"]
    assert caps.cool and caps.heat and caps.dry and caps.fan and caps.auto


def test_ct01mau_4087_profile_and_gating():
    caps = caps_mod.parse_device_info(MAU_4087)
    assert caps.num_indoor_units == 2
    assert caps.vane == 4
    assert caps.supports_swing is True
    assert caps.fan_steps == 4
    assert caps.fan_modes() == ["quiet", "low", "medium", "high", "auto"]


def test_theater_gating():
    caps = caps_mod.parse_device_info(THEATER)
    assert caps.supports_swing is True
    assert caps.fan_modes() == ["quiet", "low", "medium", "high", "auto"]
    # Theater (CT01MA) does NOT support hold — gating must reflect that.
    assert caps.hold is False
    assert caps.hvac_modes() == {"heat": True, "cool": True, "auto": True, "dry": True, "fan_only": True}
