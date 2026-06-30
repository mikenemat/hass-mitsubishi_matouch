"""Device-info (capability blob) frame serialization — locked byte-exact against the
constraint-verified MELRemo SDK model.

The capability blob is fetched as a session-3 job from IDLE, mirroring the proven
login handshake (sessions don't nest — session-0 is ended before session-3 is begun):
    begin-0 -> end-0 -> begin-3 -> data a(0,0) -> end-3
using the ordinary USER PIN (ADMIN is NOT required). These tests assert the exact
on-wire bytes for each frame so a refactor of the struct builders or the framing/
checksum can't silently re-break the sequence (which is hard to debug live, and which
once took the whole fleet offline — see the v0.14.12 outage). NOTE: the `end sess0`
frame (message_type 0x0003) is used as the mid-sequence end-0 here; its bytes are the
same regardless of position, so the per-frame assertions below still hold.

Reference frames (PIN 0x3C7F, message_id 0), verified byte-exact against the decompiled
SDK packer (sdk/a.java + model/m0 layouts):
    begin sess0   0b 00 00 01 00 01 7f 3c 00 00 00 c8 00
    begin sess3   0b 00 00 01 03 01 7f 3c 00 00 00 cb 00
    data a(0,0)   06 00 00 05 00 00 0b 00
    end sess3     0b 00 00 03 03 01 7f 3c 00 00 00 cd 00
    end sess0     0b 00 00 03 00 01 7f 3c 00 00 00 ca 00

Loads the btmatouch structs WITHOUT importing the bleak-dependent package.

    pytest tests/test_device_info_frame.py
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


_load("_adapters")
_load("const")
S = _load("_structures")

PIN = 0x3C7F  # -> little-endian body bytes 7f 3c


def _auth(mt, flag, pin):
    return S._MAAuthenticatedRequest(message_type=mt, request_flag=flag, pin=pin).to_bytes()


def _status(mt, flag):
    return S._MAStatusRequest(message_type=mt, request_flag=flag).to_bytes()


def _wire(body: bytes, message_id: int = 0) -> bytes:
    """Replicate Thermostat._async_write_request framing: 2-byte LE length + 1-byte id +
    body + checksum (sum(header+body) & 0xff, stored as 2-byte LE footer)."""
    length = 1 + len(body) + 2
    header = S._MAMessageHeader(length=length, message_id=message_id).to_bytes()
    msg = header + body
    footer = S._MAMessageFooter(crc=(sum(msg) & 0xFF)).to_bytes()
    return msg + footer


def test_device_info_bodies_byte_exact():
    MT = S._MAMessageType
    assert _auth(MT.LOGIN_REQUEST, 0x01, PIN).hex() == "0100017f3c000000"     # begin sess0
    assert _auth(MT.BEGIN_SESSION_3, 0x01, PIN).hex() == "0103017f3c000000"   # begin sess3
    assert _status(MT.DEVICE_INFO_REQUEST, 0x00).hex() == "050000"            # data a(0,0)
    assert _auth(MT.END_SESSION_3, 0x01, PIN).hex() == "0303017f3c000000"     # end sess3
    assert _auth(MT.UNKNOWN_1, 0x01, PIN).hex() == "0300017f3c000000"         # end sess0


def test_device_info_full_wire_byte_exact():
    MT = S._MAMessageType
    assert _wire(_auth(MT.LOGIN_REQUEST, 0x01, PIN)).hex() == "0b0000010 0017f3c000000c800".replace(" ", "")
    assert _wire(_auth(MT.BEGIN_SESSION_3, 0x01, PIN)).hex() == "0b000001 03017f3c000000cb00".replace(" ", "")
    assert _wire(_status(MT.DEVICE_INFO_REQUEST, 0x00)).hex() == "06000005 00000b00".replace(" ", "")
    assert _wire(_auth(MT.END_SESSION_3, 0x01, PIN)).hex() == "0b000003 03017f3c000000cd00".replace(" ", "")
    assert _wire(_auth(MT.UNKNOWN_1, 0x01, PIN)).hex() == "0b000003 00017f3c000000ca00".replace(" ", "")


def test_result_codes_corrected():
    """0x02 is RESTART_JOB (transient), NOT a bad PIN; the real bad PIN is 0x0A."""
    R = S._MAResult
    assert R.SUCCESS.value == 0x00
    assert R.RESTART_JOB.value == 0x02
    assert R.BAD_PIN.value == 0x0A
    # The old mislabels must be gone so nothing maps 0x02 -> auth failure again.
    assert not hasattr(R, "UNKNOWN_3_BAD_PIN")
    assert R.BAD_PIN.value != 0x02
