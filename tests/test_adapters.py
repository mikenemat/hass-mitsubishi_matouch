"""Tests for the _MATemperature wire codec (no Home Assistant deps).

    pytest tests/test_adapters.py
"""

import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_MOD = os.path.join(_HERE, "..", "custom_components", "mitsubishi_matouch", "btmatouch", "_adapters.py")
_spec = importlib.util.spec_from_file_location("ma_adapters", _MOD)
a = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(a)
T = a._MATemperature


def test_decode_known_values():
    # Per-nibble packed decimal: reverse the 2 LE bytes, read hex digits as decimal, /10.
    assert T.decode(bytes([0x25, 0x02])) == 22.5
    assert T.decode(bytes([0x20, 0x02])) == 22.0
    assert T.decode(bytes([0x10, 0x02])) == 21.0


def test_encode_decode_roundtrip():
    for c in (16.0, 19.5, 20.0, 22.5, 23.0, 30.5, 31.0):
        assert T.decode(T.encode(c)) == c


def test_unset_sentinel_decodes_to_none():
    # 0xFFFF = device "not set" (e.g. an auto/setback setpoint a unit doesn't use).
    # Must be None, not a float("ffff") ValueError that would fail the whole poll.
    assert T.decode(b"\xff\xff") is None
