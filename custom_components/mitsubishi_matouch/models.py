"""Models for the mitsubishi_matouch integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from .coordinator import MACoordinator


@dataclass
class MARuntimeData:
    """Runtime data for the parent entry: one coordinator per thermostat subentry."""

    coordinators: dict[str, "MACoordinator"] = field(default_factory=dict)
    # Snapshots so the update listener can diff what actually changed.
    options: dict = field(default_factory=dict)
    subentry_data: dict[str, dict] = field(default_factory=dict)
    # mac -> monotonic time of last rebalance bounce (anti-thrash).
    rebalance_cooldown: dict[str, float] = field(default_factory=dict)
    # Serializes concurrent update-listener invocations (add/remove/reconfigure).
    update_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Debouncer that single-flights + coalesces rebalance runs.
    rebalancer: Any = None
    # True while a rebalance sweep is in flight (explicit single-flight guard).
    rebalancing: bool = False


type MAConfigEntry = ConfigEntry[MARuntimeData]
