"""Adapters for the btmatouch library."""

from construct_typed import Adapter, Context

__all__: list[str] = []


class _MATemperature(Adapter[bytes, bytes, float, float]):
    """Adapter to encode and decode temperature data."""

    # The device's "not set / not applicable" sentinel for a temperature field (e.g. an
    # auto/setback setpoint or a min/max bound a given unit/mode doesn't use). Confirmed
    # by decompiling the MELRemo SDK (model/m0/o/b0.java treats 0xFFFF as null). Decoding
    # it naively does float("ffff") -> ValueError, which would fail the WHOLE status
    # parse and drop the unit offline; map it to None instead.
    _UNSET = b"\xff\xff"

    def _encode(self, obj: float, _ctx: Context, _path: str) -> bytes:
        return self.encode(obj)

    def _decode(self, obj: bytes, _ctx: Context, _path: str) -> float | None:
        return self.decode(obj)

    @classmethod
    def encode(cls, value: float) -> bytes:
        return int(str(int(round(value*2)/2*10)), 16).to_bytes(2, "little")

    @classmethod
    def decode(cls, value: bytes) -> float | None:
        if value == cls._UNSET:
            return None
        return float(bytes(reversed(value)).hex())/10
