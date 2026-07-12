"""Append-only JSONL telemetry log + in-memory ring buffer for R&D export.

This decouples data capture from retrieval: every connect/disconnect/poll event is
appended to a JSONL file in the HA config directory (portable, machine-readable) and
kept in a ring buffer for instant inclusion in the diagnostics download. The file can
be pulled back to Claude for analysis (SSH/scp, the device "Download diagnostics"
button, or the HA REST API). Telemetry must never break polling, so all writes are
off-loop and failures are swallowed.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections import deque
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

TELEMETRY_FILENAME = "mitsubishi_matouch_telemetry.jsonl"
_MAX_BYTES = 5 * 1024 * 1024  # rotate at ~5 MB to bound disk use
_BACKUPS = 3  # rotation generations kept so a storm can't wipe pre-storm history
_RING = 1000


class MATelemetryLog:
    """Records connection telemetry to JSONL and an in-memory ring buffer."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the telemetry log."""

        self._hass = hass
        self._path = hass.config.path(TELEMETRY_FILENAME)
        self._recent: deque[dict[str, Any]] = deque(maxlen=_RING)
        self._write_lock = threading.Lock()

    @property
    def path(self) -> str:
        """Absolute path of the JSONL telemetry file."""

        return self._path

    def recent(self, mac: str | None = None, limit: int = 300) -> list[dict[str, Any]]:
        """Return the most recent buffered events, optionally filtered by MAC."""

        if mac is None:
            items = list(self._recent)
        else:
            mac_u = mac.upper()
            items = [event for event in self._recent if event.get("mac") == mac_u]
        return items[-limit:]

    async def record(self, event: str, mac: str, persist: bool = True, **fields: Any) -> None:
        """Record one telemetry event.

        Always kept in the in-memory ring buffer (for live get_telemetry pulls and
        diagnostics). Only written to the JSONL file when persist=True, so routine
        per-poll events can be kept out of the file for sustainable 24/7 logging.
        Non-blocking; never raises to the caller.
        """

        record: dict[str, Any] = {
            "ts": dt_util.utcnow().isoformat(timespec="milliseconds"),
            "event": event,
            "mac": mac.upper(),
        }
        record.update(fields)
        self._recent.append(record)
        if not persist:
            return
        try:
            await self._hass.async_add_executor_job(self._append, json.dumps(record, default=str))
        except Exception as ex:  # noqa: BLE001 - telemetry must never break polling
            _LOGGER.debug("telemetry write failed: %s", ex)

    def _append(self, line: str) -> None:
        """Append one JSON line, rotating the file if it grew too large.

        Runs in an executor thread; the lock serializes concurrent writers (one
        per device) so lines never interleave.
        """

        with self._write_lock:
            try:
                if os.path.exists(self._path) and os.path.getsize(self._path) > _MAX_BYTES:
                    # Shift generations: .(_BACKUPS-1) -> .(_BACKUPS), ... , base -> .1
                    for index in range(_BACKUPS, 0, -1):
                        src = self._path if index == 1 else f"{self._path}.{index - 1}"
                        if os.path.exists(src):
                            os.replace(src, f"{self._path}.{index}")
            except OSError:
                pass
            with open(self._path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
