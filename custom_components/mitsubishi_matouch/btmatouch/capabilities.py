"""Per-unit capability parsing for the MA Touch device-info (capability) blob.

The device-info job (begin-0 -> end-0 -> begin-3 -> data a(0,0) -> end-3, run from
IDLE — see Thermostat.async_get_device_info) returns a capability blob that the
MELRemo SDK models as `model/m0/i/a`. This module parses it.

Wire model (decompiled SDK `sdk/a.java`, confirmed byte-exact against a live CT01MA
blob): the field list is bit-packed by grouping fields left-to-right until the running
width reaches >= 8 bits, REVERSING each group, concatenating the groups, and writing
bits MSB-first. Sub-structures (mode_exist=`o/d0`, fu_func=`o/n`, ic_info=`o/q`) are
packed the same way within their own bit span. `settemp_range` (`o/y`) is left opaque
here — the status frame already carries the live setpoint ranges.

The blob is the trailing `model/m0/i/a` structure of the data-frame response; the
response prefixes it with a 6-byte header (L2 phase + echoed L3 major/sub + 2-byte
result + 1). m0/i/a is 76 bytes, or 77 when the device advertises the
ELECTRICITY_CONSUMPTION_AND_AI capability (a trailing byte).

This is a pure-bytes parser (no construct/bleak deps) so it is unit-testable in
isolation against a captured blob.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["Capabilities", "IndoorUnit", "parse_device_info", "parse_capability_blob"]

# Response header length before the m0/i/a structure (phase 1 + L3 major/sub echo 2 +
# result 2 + 1). Validated against the live CT01MA device-info response.
_RESPONSE_HEADER_LEN = 6
# m0/i/a structure size without / with the optional electricity+AI trailing byte.
_BLOB_LEN = 76
_BLOB_LEN_WITH_ELEC = 77

_CONNECT_UNIT = {0: "slim", 1: "multi", 2: "vent_ac"}
# temp_cf: 0 = 0.5C step, 1 = 1C step, 2 = Fahrenheit, 3 = 2F step.
_TEMP_UNIT = {0: "C", 1: "C", 2: "F", 3: "F"}
_TEMP_STEP_C = {0: 0.5, 1: 1.0}
# fanstep: number of selectable fan speeds (excludes auto).
_FAN_STEPS = {0: 0, 1: 2, 2: 3, 3: 4}
# right_left horizontal vane resolution.
_RIGHT_LEFT_STEPS = {0: 0, 1: 3, 2: 7}


def _extract(data: bytes, bit_off: int, width: int) -> int:
    """Read `width` bits starting at absolute bit offset `bit_off`, MSB-first."""
    val = 0
    for k in range(width):
        b = bit_off + k
        val = (val << 1) | ((data[b // 8] >> (7 - (b % 8))) & 1)
    return val


def _physical_layout(spec: list[tuple[str, int]]) -> dict[str, tuple[int, int]]:
    """Replicate sdk/a.java: group fields until the running width >= 8, reverse each
    group, concatenate; return {name: (bit_offset, width)} with MSB-first offsets."""
    groups: list[list[tuple[str, int]]] = []
    cur: list[tuple[str, int]] = []
    acc = 0
    for name, w in spec:
        cur.append((name, w))
        acc += w
        if acc >= 8:
            groups.append(list(reversed(cur)))
            cur, acc = [], 0
    if cur:
        groups.append(list(reversed(cur)))
    layout: dict[str, tuple[int, int]] = {}
    bit = 0
    for g in groups:
        for name, w in g:
            layout[name] = (bit, w)
            bit += w
    return layout


def _parse(spec: list[tuple[str, int]], data: bytes, base_bit: int = 0) -> dict[str, int]:
    return {name: _extract(data, base_bit + bit, w) for name, (bit, w) in _physical_layout(spec).items()}


# --- field specs, verbatim field order + widths from the decompiled SDK ---
_D0 = [  # mode_exist_mx (16b)
    ("cool", 1), ("dry", 1), ("fan", 1), ("heat", 1), ("setback", 1),
    ("auto", 2), ("burnheat", 1), ("reserved", 8),
]
_N = [  # fu_func_mx (16b)
    ("fansp_auto", 1), ("hum", 1), ("night_purge", 1), ("state_24h", 1),
    ("roomtemp_type", 2), ("mode_disp", 1), ("settemp_disp", 1), ("reserved", 8),
]
_Q = [  # ic_info_mx_NN (24b each)
    ("data", 1), ("reserved", 1), ("con_unit_type", 3), ("unit_type", 2),
    ("reibaisyu", 1), ("clean", 1), ("elevation", 1), ("outsilent", 1),
    ("vanelock", 1), ("power_collect", 1), ("move_eye_enable", 1),
    ("move_eye_stop_enable", 1), ("fanblock", 1), ("address", 8),
]
_IC_COUNT = 16
_TOP = (
    [
        ("reserved01", 5), ("connect_unit", 3), ("mode_exist", 16), ("settemp_range", 160),
        ("fanstep", 3), ("fanauto", 1), ("vane", 3), ("louver", 1),
        ("lossnai", 1), ("temp_cf", 2), ("ak_setting", 1), ("reserved02", 1),
        ("savefunc", 1), ("roomtemp01", 1), ("holdfunc", 1),
        ("min_range_setting", 4), ("right_left", 2), ("move_eye", 1), ("reserved03", 1),
        ("fu_func", 16),
    ]
    + [(f"ic_info_{i:02d}", 24) for i in range(_IC_COUNT)]
)
_TOP_LAYOUT = _physical_layout(_TOP)


@dataclass(frozen=True)
class IndoorUnit:
    """One present indoor unit advertised in the capability blob."""

    index: int
    address: int
    clean: bool = False          # auto-clean supported
    outsilent: bool = False      # outdoor-silent supported
    move_eye_enable: bool = False
    con_unit_type: int = 0
    unit_type: int = 0


@dataclass(frozen=True)
class Capabilities:
    """Decoded per-thermostat capabilities (static hardware facts)."""

    connect_unit: str            # "slim" / "multi" / "vent_ac"
    # supported HVAC modes
    cool: bool
    dry: bool
    fan: bool
    heat: bool
    auto: bool                   # auto_mb != 0
    auto_kind: int               # 0 none / 1 one-setpoint / 2 two-setpoint
    setback: bool
    # fan / vane / louver / vent / horizontal-vane / move-eye
    fan_steps: int               # selectable speeds (0 if none)
    fan_auto: bool
    vane: int                    # 0 none .. (4=5-step+swing typical)
    louver: bool
    lossnai: bool                # ventilation (Lossnay)
    right_left_steps: int        # horizontal vane (0 none / 3 / 7)
    move_eye: bool
    # display / misc
    temp_unit: str               # "C" / "F"
    temp_step_c: float           # setpoint step in C (0.5 or 1.0); 0.5 when in F
    hold: bool                   # holdfunc supported
    savefunc: bool
    roomtemp_01: bool            # 0.1-degree room-temp resolution
    electricity: bool            # energy reporting capability
    ai: bool
    indoor_units: tuple[IndoorUnit, ...] = ()
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def num_indoor_units(self) -> int:
        return len(self.indoor_units)

    @property
    def supports_swing(self) -> bool:
        """Whether this unit has a controllable (swingable) vertical vane."""
        return self.vane > 0

    def fan_modes(self) -> list[str]:
        """HA fan-mode strings this unit supports, by fan-step count + fan-auto.

        The string values equal Home Assistant's FAN_LOW/MEDIUM/HIGH/AUTO constants
        (kept as literals here so this module needs no HA import). Gating these is the
        fix for the 'quiet'/speed-revert wart: a 3-step unit never offers 'quiet'.
        """
        speeds = {
            2: ["low", "high"],
            3: ["low", "medium", "high"],
            4: ["quiet", "low", "medium", "high"],
        }
        modes = list(speeds.get(self.fan_steps, ["low", "medium", "high"]))
        if self.fan_auto:
            modes.append("auto")
        return modes

    def hvac_modes(self) -> dict[str, bool]:
        """Supported-mode flags keyed by the lowercase HVAC name (off always implied)."""
        return {
            "heat": self.heat, "cool": self.cool, "auto": self.auto,
            "dry": self.dry, "fan_only": self.fan,
        }

    def as_dict(self) -> dict:
        """Flat, JSON-friendly view (for diagnostics / the fetch service)."""
        return {
            "connect_unit": self.connect_unit,
            "modes": {
                "cool": self.cool, "dry": self.dry, "fan": self.fan,
                "heat": self.heat, "auto": self.auto, "auto_kind": self.auto_kind,
                "setback": self.setback,
            },
            "fan_steps": self.fan_steps,
            "fan_auto": self.fan_auto,
            "vane": self.vane,
            "louver": self.louver,
            "lossnai": self.lossnai,
            "right_left_steps": self.right_left_steps,
            "move_eye": self.move_eye,
            "temp_unit": self.temp_unit,
            "temp_step_c": self.temp_step_c,
            "hold": self.hold,
            "savefunc": self.savefunc,
            "roomtemp_01": self.roomtemp_01,
            "electricity": self.electricity,
            "ai": self.ai,
            "num_indoor_units": self.num_indoor_units,
            "indoor_units": [
                {
                    "index": u.index, "address": u.address, "clean": u.clean,
                    "outsilent": u.outsilent, "move_eye_enable": u.move_eye_enable,
                    "con_unit_type": u.con_unit_type, "unit_type": u.unit_type,
                }
                for u in self.indoor_units
            ],
        }


def parse_capability_blob(blob: bytes) -> Capabilities:
    """Parse the 76/77-byte m0/i/a structure itself (no response header)."""

    if len(blob) < _BLOB_LEN:
        raise ValueError(f"capability blob too short: {len(blob)} (need >= {_BLOB_LEN})")

    top = {name: _extract(blob, bit, w) for name, (bit, w) in _TOP_LAYOUT.items()}
    modes = _parse(_D0, blob, _TOP_LAYOUT["mode_exist"][0])
    fu = _parse(_N, blob, _TOP_LAYOUT["fu_func"][0])

    units: list[IndoorUnit] = []
    for i in range(_IC_COUNT):
        u = _parse(_Q, blob, _TOP_LAYOUT[f"ic_info_{i:02d}"][0])
        if u["data"] == 1:
            units.append(
                IndoorUnit(
                    index=i, address=u["address"], clean=bool(u["clean"]),
                    outsilent=bool(u["outsilent"]), move_eye_enable=bool(u["move_eye_enable"]),
                    con_unit_type=u["con_unit_type"], unit_type=u["unit_type"],
                )
            )

    elec = ai = False
    if len(blob) >= _BLOB_LEN_WITH_ELEC:
        # Optional trailing byte: electricity_consumption(1) + ai(1) + reserved(6),
        # reversed per the packer -> reserved(bits7-2), ai(bit1), elec(bit0).
        tail = blob[_BLOB_LEN]
        elec = bool(tail & 0x01)
        ai = bool((tail >> 1) & 0x01)

    temp_cf = top["temp_cf"]
    return Capabilities(
        connect_unit=_CONNECT_UNIT.get(top["connect_unit"], f"unknown_{top['connect_unit']}"),
        cool=bool(modes["cool"]), dry=bool(modes["dry"]), fan=bool(modes["fan"]),
        heat=bool(modes["heat"]), auto=modes["auto"] != 0, auto_kind=modes["auto"],
        setback=bool(modes["setback"]),
        fan_steps=_FAN_STEPS.get(top["fanstep"], 0), fan_auto=bool(top["fanauto"]),
        vane=top["vane"], louver=bool(top["louver"]), lossnai=bool(top["lossnai"]),
        right_left_steps=_RIGHT_LEFT_STEPS.get(top["right_left"], 0),
        move_eye=bool(top["move_eye"]),
        temp_unit=_TEMP_UNIT.get(temp_cf, "C"), temp_step_c=_TEMP_STEP_C.get(temp_cf, 0.5),
        hold=bool(top["holdfunc"]), savefunc=bool(top["savefunc"]),
        roomtemp_01=bool(top["roomtemp01"]),
        electricity=elec, ai=ai,
        indoor_units=tuple(units),
        raw={"top": top, "modes": modes, "fu_func": fu},
    )


def parse_device_info(response: bytes) -> Capabilities:
    """Parse a full device-info DATA-frame response (validate=False bytes): strip the
    6-byte response header, then parse the m0/i/a capability structure."""

    if len(response) < _RESPONSE_HEADER_LEN + _BLOB_LEN:
        raise ValueError(f"device-info response too short: {len(response)} bytes")
    return parse_capability_blob(response[_RESPONSE_HEADER_LEN:])
