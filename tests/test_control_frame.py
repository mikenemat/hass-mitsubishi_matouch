"""Control-frame serialization tests for the new axes (louver/vent/hold/right-left/
move-eye). Loads the btmatouch structs WITHOUT importing the bleak-dependent package
(_structures only needs construct + _adapters + const).

    pytest tests/test_control_frame.py
"""

import importlib.util
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_BT = os.path.join(_HERE, "..", "custom_components", "mitsubishi_matouch", "btmatouch")

# Build a synthetic package so _structures' relative imports (._adapters, .const) resolve
# without triggering btmatouch/__init__.py (which imports bleak).
_pkg = types.ModuleType("btx")
_pkg.__path__ = [_BT]
sys.modules["btx"] = _pkg


def _load(name):
    spec = importlib.util.spec_from_file_location(f"btx.{name}", os.path.join(_BT, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"btx.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


_load("_adapters")
S = None
_load("const")
S = _load("_structures")


def _control(**kw):
    defaults = dict(
        message_type=S._MAMessageType.CONTROL_REQUEST, request_flag=0x01,
        flags_a=0, flags_b=0, flags_c=0,
        operation_mode_flags=S._MAOperationModeFlags.NONE,
        cool_setpoint=0, heat_setpoint=0,
        unknown_setpoint_1=0, unknown_setpoint_2=0, unknown_setpoint_3=0,
        vane_fan_mode=0, louver_vent=0, hold_rl_move_eye=0,
    )
    defaults.update(kw)
    return S._MAControlRequest(**defaults).to_bytes()


# Body byte indices: [0:2]=message_type, 2=request_flag, 3=flags_a, 4=flags_b,
# 5=flags_c, 6=op_mode, 7..16=5 setpoints, 17=vane_fan, 18=louver_vent, 19=hold_rl_move_eye.

def test_frame_length_and_layout():
    body = _control()
    assert len(body) == 20
    assert body[0:2] == bytes([0x05, 0x01])  # CONTROL_REQUEST 0x0105 LE
    assert body[2] == 0x01                     # request_flag


def test_hold_on():
    body = _control(flags_c=0x10, hold_rl_move_eye=0x01)
    assert body[5] == 0x10 and body[18] == 0x00 and body[19] == 0x01


def test_louver_on():
    # louver_vent = (vent<<4)|louver -> louver on, vent 0 = 0x01
    body = _control(flags_c=0x04, louver_vent=0x01)
    assert body[5] == 0x04 and body[18] == 0x01 and body[19] == 0x00


def test_vent_high():
    # vent HIGH=2 -> (2<<4)|0 = 0x20
    body = _control(flags_c=0x08, louver_vent=0x20)
    assert body[5] == 0x08 and body[18] == 0x20


def test_right_left_right():
    # right_left RIGHT=6 -> (6<<1) = 0x0C in the low byte
    body = _control(flags_c=0x20, hold_rl_move_eye=(6 << 1))
    assert body[5] == 0x20 and body[19] == 0x0C


def test_move_eye_auto():
    # move_eye AUTO=4 -> (4<<4) = 0x40
    body = _control(flags_c=0x40, hold_rl_move_eye=(4 << 4))
    assert body[5] == 0x40 and body[19] == 0x40


def test_value_byte_formulas():
    # The packing the thermostat methods use, matching the SDK control body.
    def louver_vent(vent, louver):
        return ((vent & 0x0F) << 4) | (louver & 0x0F)

    def hold_rl_eye(move_eye, right_left, hold):
        return ((move_eye & 0x07) << 4) | ((right_left & 0x07) << 1) | (hold & 0x01)

    assert louver_vent(0, 1) == 0x01     # louver on
    assert louver_vent(2, 0) == 0x20     # vent high
    assert hold_rl_eye(0, 0, 1) == 0x01  # hold on
    assert hold_rl_eye(0, 6, 0) == 0x0C  # right-left = right
    assert hold_rl_eye(4, 0, 0) == 0x40  # move-eye = auto
