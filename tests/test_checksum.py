"""Wire-checksum spec for MA Touch response frames.

The controller appends a 2-byte little-endian checksum to every frame, equal to
``sum(all bytes before the checksum)`` taken as a 16-bit value (the 2 length-header
bytes are included in the sum). This was verified live on 2026-06-26 by capturing
raw ``RCV:`` frames from five physical units (4x CT01MAU + 1x CT01MA).

``btmatouch/thermostat._on_message_received`` validates this inline and rejects a
mismatch as a retryable corrupt frame (``MAResponseException``). This test locks the
formula against those real captures so a refactor can't silently break it -- a
mismatch would otherwise be misread (e.g. a stray 0x02 -> spurious "bad PIN").
"""

import pytest


def crc16le(frame: bytes) -> int:
    """Expected checksum of a complete on-wire frame (len + id + body + crc)."""
    return sum(frame[:-2]) & 0xFFFF


def checksum_ok(frame: bytes) -> bool:
    if len(frame) < 5:  # 2 len + 1 id + 0 body + 2 crc, minimum
        return False
    return int.from_bytes(frame[-2:], "little") == crc16le(frame)


# Real frames captured from the live units (reassembled from 20-byte BLE chunks).
CAPTURED = {
    "CT01MAU-2D:15": "35000c0500020000000a00039001800270018002"
    "9001000390018002700125022502250225022502"
    "40030000000001250201001005b705",
    "CT01MAU-2D:27": "35000c0500020000000a00039001800270018002"
    "9001000390018002700125022502250225022502"
    "40030000000000250201001004b505",
    "CT01MAU-2D:1C": "35000c0500020000000a00039001800270018002"
    "9001000390018002700125022502250225022502"
    "40000000000000250201001004b205",
    "CT01MA-28:E9": "3c000c0500020000000a10036001850200011003"
    "6001000000000000000025028001900100000000"
    "4006000000000025020100080430500220024002"
    "6204",
    # An outbound STATUS request we built ourselves (sum < 256, so it doubles as
    # a check that send/receive agree on the formula for small frames).
    "SND-request": "0600040502001100",
    # An outbound ADMIN begin-0 login frame (byte-sum 559 > 255). This is the frame
    # class that exposed the outbound-checksum bug: thermostat._crc_sum truncated to
    # `& 0xff` and shipped crc "2f 00", but the correct 16-bit crc is "2f 02", so the
    # device silently dropped every admin/service login. Locks the 16-bit rule for
    # large-sum outbound frames. len=0b00, id=00, body=01 00 02 bc 32 01 99 99 (ADMIN,
    # user_pw 0x32BC, license_type 1, license_pw 0x9999), crc=022f LE.
    "SND-admin-login": "0b0000010002bc320199992f02",
}


@pytest.mark.parametrize("name", list(CAPTURED))
def test_captured_frames_pass_checksum(name):
    assert checksum_ok(bytes.fromhex(CAPTURED[name])), name


def test_corrupted_result_byte_is_rejected():
    """The exact spurious-BAD_PIN scenario: a flipped result byte must fail the
    checksum instead of being acted on as a wrong PIN."""
    good = bytearray.fromhex(CAPTURED["CT01MAU-2D:15"])
    assert checksum_ok(bytes(good))
    corrupted = bytearray(good)
    corrupted[3] = 0x02  # result byte (first payload byte) -> "bad PIN"
    assert not checksum_ok(bytes(corrupted))


def test_any_single_bit_flip_is_caught():
    good = bytes.fromhex(CAPTURED["CT01MA-28:E9"])
    for i in range(len(good) - 2):  # leave the checksum bytes themselves intact
        flipped = bytearray(good)
        flipped[i] ^= 0x01
        assert not checksum_ok(bytes(flipped)), f"flip at byte {i} slipped through"


def test_runt_frame_is_rejected():
    assert not checksum_ok(b"\x06\x00")


def test_large_sum_frame_needs_16bit_checksum():
    """Regression for the outbound checksum bug: a frame whose byte-sum exceeds 255
    MUST carry the full 16-bit checksum. An `& 0xff` truncation (the old
    thermostat._crc_sum) would ship the wrong high byte and the device drops the
    frame silently -- which is what blocked admin/service login."""
    frame = bytes.fromhex(CAPTURED["SND-admin-login"])
    body_sum = crc16le(frame)              # sum(all bytes before crc), 16-bit
    assert body_sum > 0xFF, "sample must actually exceed the 8-bit range"
    assert body_sum & 0xFF != body_sum, "8-bit truncation would change the value"
    # The frame's stored checksum is the correct 16-bit value...
    assert checksum_ok(frame)
    # ...and an 8-bit-truncated checksum (the bug) would FAIL validation.
    truncated = frame[:-2] + (body_sum & 0xFF).to_bytes(2, "little")
    assert not checksum_ok(truncated)
