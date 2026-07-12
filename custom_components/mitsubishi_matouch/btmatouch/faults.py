"""MA Touch error-history (fault) record decoding — model/m0/o/g0.

The session-5 error-history response is a sequence of 11-byte `g0` records. This decodes
one record into a human-readable fault: the Mitsubishi check code (e.g. "E4"/"E2"), the
faulting indoor-unit address, and the timestamp.

Byte layout independently re-derived from the SDK (g0.java field spec + a.java packer)
and cross-checked by the `g0-fault-record-verify` workflow. It reuses the SAME bit-unpacker
as the capability parser, which is live-validated (later-declared field of a reversed group
lands in the more-significant bits — e.g. byte1 = err_code_type4_L2 hi nibble / L1 lo nibble).

⚠️ HALFWAY (2026-06-30): `decode_fault_record` + `parse_fault_records` are complete and
unit-tested against cross-checked vectors. What is NOT yet done — because it needs a
booted-but-faulted unit to validate against (e.g. a *runtime* E2 on Theater; a blocking
startup fault like Server Room's E4 can't be read, it's surfaced via the device-fault
Repairs notice instead): the exact live RESPONSE framing — the header offset before the
record array, and whether the unit uses the HA-device path (one record per indexed request,
m0/n/g) or the legacy path (up to 16 records concatenated in one response, m0/n/i). Until
that's captured, DO NOT wire a live fault-code sensor off this. See RE_WISHLIST.md §2(b).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .capabilities import _physical_layout, _extract

__all__ = ["FaultRecord", "decode_fault_record", "parse_fault_records", "FAULT_RECORD_LEN"]

FAULT_RECORD_LEN = 11

# g0 field spec — verbatim order + widths from model/m0/o/g0.java (88 bits = 11 bytes).
_G0_SPEC = [
    ("data_mb", 1), ("reserved", 7),
    ("type4_L1", 4), ("type4_L2", 4), ("type4_H1", 4), ("type4_H2", 4),
    ("type2_L", 5), ("type2_H", 3),
    ("source_address", 8), ("source_goki", 8),
    ("year", 8), ("month", 8), ("day", 8), ("hour", 8), ("min", 8),
]
_G0_LAYOUT = _physical_layout(_G0_SPEC)

# Alpha check-code maps (g0.java d()): prefix from err_code_type2_H, suffix from type2_L.
_PREFIX = {0: "A", 1: "b", 2: "E", 3: "F", 4: "J", 5: "L", 6: "P", 7: "U"}
_SUFFIX = {10: "A", 11: "B", 12: "C", 13: "D", 14: "E", 15: "F",
           16: "O", 17: "H", 18: "J", 19: "L", 20: "P", 21: "U"}


@dataclass(frozen=True)
class FaultRecord:
    """One decoded error-history entry."""

    code: str                    # alpha check code, e.g. "E4" ("--" if unmappable)
    numeric_code: str            # e.g. "0004", or "----" when not applicable
    source_address: int          # MNET address of the faulting indoor unit
    unit_number: int             # source_goki
    timestamp: datetime | None   # None if the stored date is invalid/unset
    raw: dict = field(default_factory=dict, repr=False)

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "numeric_code": self.numeric_code,
            "source_address": self.source_address,
            "unit_number": self.unit_number,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


def _alpha(h: int, low: int) -> str:
    """Mitsubishi alpha check code from type2_H (prefix) + type2_L (suffix)."""
    prefix = _PREFIX.get(h)
    if prefix is None:
        return "--"
    if low in _SUFFIX:
        return prefix + _SUFFIX[low]
    if 1 <= low <= 9:
        return prefix + str(low)
    return "--"


def _numeric(l1: int, l2: int, h1: int, h2: int) -> str:
    """4-digit numeric check code (g0.java e())."""
    if l1 >= 10 or l2 >= 10 or h1 >= 10 or h2 >= 8:
        return "----"
    return "%04d" % (h2 * 1000 + h1 * 100 + l2 * 10 + l1)


def _timestamp(year: int, month: int, day: int, hour: int, minute: int) -> datetime | None:
    """Record timestamp (g0.java g()): null on the SDK's invalid-date conditions, and also
    on anything Python's stricter datetime rejects (e.g. month/day 0, hour 24)."""
    if year >= 100 or month > 12 or day > 31 or hour > 24 or minute >= 60:
        return None
    try:
        return datetime(2000 + year, month, day, hour, minute)
    except ValueError:
        return None


def decode_fault_record(record: bytes) -> FaultRecord:
    """Decode one 11-byte g0 error-history record (valid iff raw['data_mb'] == 1)."""
    if len(record) < FAULT_RECORD_LEN:
        raise ValueError(f"fault record too short: {len(record)} (need {FAULT_RECORD_LEN})")
    f = {name: _extract(record, bit, w) for name, (bit, w) in _G0_LAYOUT.items()}
    return FaultRecord(
        code=_alpha(f["type2_H"], f["type2_L"]),
        numeric_code=_numeric(f["type4_L1"], f["type4_L2"], f["type4_H1"], f["type4_H2"]),
        source_address=f["source_address"],
        unit_number=f["source_goki"],
        timestamp=_timestamp(f["year"], f["month"], f["day"], f["hour"], f["min"]),
        raw=f,
    )


def parse_fault_records(blob: bytes) -> list[FaultRecord]:
    """Split a concatenated record array into 11-byte g0 records and return the VALID ones
    (data_mb == 1). `blob` is the record-array portion of the session-5 response with the
    response header already stripped by the caller — see the HALFWAY note above; that
    stripping/path-selection is the piece still pending a live readable fault."""
    out: list[FaultRecord] = []
    for i in range(0, len(blob) - FAULT_RECORD_LEN + 1, FAULT_RECORD_LEN):
        rec = decode_fault_record(blob[i:i + FAULT_RECORD_LEN])
        if rec.raw.get("data_mb") == 1:
            out.append(rec)
    return out
