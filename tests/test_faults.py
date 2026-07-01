"""Error-history (g0) fault-record decoder tests.

Locked against test vectors produced by the `g0-fault-record-verify` workflow, which
derived the 11-byte g0 layout INDEPENDENTLY from the SDK (two agents + synthesis) — so
these fixtures aren't circular with the decoder (they weren't encoded with the same code
that decodes them). The layout is the same reversed-group / MSB-first bit-packing that the
capability parser uses (live-validated), applied to g0's field spec.

    pytest tests/test_faults.py
"""

import importlib.util
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_BT = os.path.join(_HERE, "..", "custom_components", "mitsubishi_matouch", "btmatouch")

_pkg = types.ModuleType("btx")
_pkg.__path__ = [_BT]
sys.modules["btx"] = _pkg


def _load(name):
    spec = importlib.util.spec_from_file_location(f"btx.{name}", os.path.join(_BT, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"btx.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


_load("capabilities")   # faults imports the bit-unpacker from here
faults = _load("faults")


# Cross-checked vectors from the workflow (hex = 11 bytes, byte 0 first).
def test_e4_valid_record():
    r = faults.decode_fault_record(bytes.fromhex("01040044050018060f0d2d"))
    assert r.raw["data_mb"] == 1
    assert r.code == "E4"
    assert r.numeric_code == "0004"
    assert r.source_address == 5
    assert r.timestamp is not None
    assert (r.timestamp.year, r.timestamp.month, r.timestamp.day,
            r.timestamp.hour, r.timestamp.minute) == (2024, 6, 15, 13, 45)


def test_e2_valid_record():
    r = faults.decode_fault_record(bytes.fromhex("010200420300170c1f0805"))
    assert r.code == "E2"
    assert r.numeric_code == "0002"
    assert r.source_address == 3
    assert (r.timestamp.year, r.timestamp.month, r.timestamp.day,
            r.timestamp.hour, r.timestamp.minute) == (2023, 12, 31, 8, 5)


def test_invalid_record_is_dropped():
    # data_mb = 0 -> not a real record.
    r = faults.decode_fault_record(bytes.fromhex("0000000000000000000000"))
    assert r.raw["data_mb"] == 0
    assert r.code == "--"


def test_valid_code_invalid_timestamp():
    # F4 with month = 13 -> g() returns null.
    r = faults.decode_fault_record(bytes.fromhex("010000640700190d0f0a1e"))
    assert r.code == "F4"
    assert r.timestamp is None
    assert r.source_address == 7


def test_parse_records_filters_invalid_and_keeps_order():
    # E4 valid + invalid(all-zero) + E2 valid -> only the two valid ones, in order.
    blob = bytes.fromhex(
        "01040044050018060f0d2d"   # E4
        "0000000000000000000000"   # invalid (data_mb=0)
        "010200420300170c1f0805"   # E2
    )
    recs = faults.parse_fault_records(blob)
    assert [r.code for r in recs] == ["E4", "E2"]
    assert [r.source_address for r in recs] == [5, 3]


def test_byte3_encodes_alpha_code():
    # E4 = type2_H(2)<<5 | type2_L(4) = 0x44; E2 = 0x42 — the code lives in byte 3.
    assert bytes.fromhex("01040044050018060f0d2d")[3] == 0x44
    assert bytes.fromhex("010200420300170c1f0805")[3] == 0x42


def test_as_dict_json_friendly():
    d = faults.decode_fault_record(bytes.fromhex("01040044050018060f0d2d")).as_dict()
    assert d["code"] == "E4"
    assert d["source_address"] == 5
    assert d["timestamp"].startswith("2024-06-15T13:45")
