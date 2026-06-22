"""Distribute MA Touch BLE connections evenly across ESP32 Bluetooth proxies.

Home Assistant's native routing is RSSI-greedy: it connects through whichever
adapter/proxy hears a device loudest, capped only by each proxy's free connection
slots. With several thermostats clustered near one proxy that can pile most of the
connections onto a single radio. This balancer instead spreads our connections
across the proxies that can actually reach each thermostat (above an RSSI floor),
picking the least-loaded one. It degrades gracefully to HA's default selection if
the scanner details aren't available.
"""

import logging

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from bleak.backends.device import BLEDevice

from .const import PROXY_RSSI_FLOOR

_LOGGER = logging.getLogger(__name__)


class MAProxyBalancer:
    """Chooses the least-loaded reachable proxy for each device address."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the balancer."""

        self._hass = hass
        self._assignments: dict[str, str] = {}  # mac (upper) -> proxy source

    @property
    def assignments(self) -> dict[str, str]:
        """Current device -> proxy assignments (copy)."""

        return dict(self._assignments)

    def release(self, mac: str) -> None:
        """Forget a device's proxy assignment (on disconnect/unload)."""

        self._assignments.pop(mac.upper(), None)

    def note_connected(self, mac: str, source: str) -> None:
        """Reconcile the assignment to the proxy actually serving the live link.

        The pick() choice is advertisement-based; the real connection may land on
        a different proxy. Recording the true source keeps the load map honest.
        """

        self._assignments[mac.upper()] = source

    def assigned_source(self, mac: str) -> str | None:
        """The proxy source last assigned to a device (pick or live-connect)."""

        return self._assignments.get(mac.upper())

    def prune_source(self, source: str) -> None:
        """Drop assignments pointing at a proxy that has gone offline, so the
        load map doesn't keep crediting load to a source that no longer exists."""

        for mac in [m for m, src in self._assignments.items() if src == source]:
            self._assignments.pop(mac, None)

    def pick(self, mac: str) -> tuple[BLEDevice | None, str | None, int | None]:
        """Return (ble_device, proxy_label, rssi) for the chosen proxy.

        Falls back to HA's default device selection when per-scanner details are
        unavailable; returns (None, None, None) if the device isn't seen at all.
        """

        mac_u = mac.upper()

        try:
            scanner_devices = bluetooth.async_scanner_devices_by_address(
                self._hass, mac_u, connectable=True
            )
        except Exception as ex:  # noqa: BLE001 - API shape differences / not ready
            _LOGGER.debug("scanner_devices_by_address unavailable for %s: %s", mac_u, ex)
            scanner_devices = []

        candidates = [
            sd
            for sd in scanner_devices
            if sd.ble_device is not None and sd.advertisement is not None
        ]

        if not candidates:
            # No per-scanner data (e.g. a connected device that stopped advertising,
            # or one mid-reconnect): let HA pick the best path, but KEEP the
            # last-known assignment so the device's proxy slot stays counted in the
            # load map (popping it here made the balancer drift toward empty).
            device = bluetooth.async_ble_device_from_address(
                self._hass, mac_u, connectable=True
            )
            return device, None, None

        # Prefer proxies that hear the device above the floor; if none do, use all.
        reachable = [
            sd for sd in candidates if (sd.advertisement.rssi or -127) > PROXY_RSSI_FLOOR
        ]
        pool = reachable or candidates

        # Count current load per proxy, excluding this device's own assignment.
        load: dict[str, int] = {}
        for assigned_mac, source in self._assignments.items():
            if assigned_mac == mac_u:
                continue
            load[source] = load.get(source, 0) + 1

        # Least-loaded first, then strongest signal.
        chosen = min(
            pool,
            key=lambda sd: (load.get(sd.scanner.source, 0), -(sd.advertisement.rssi or -127)),
        )

        source = chosen.scanner.source
        self._assignments[mac_u] = source
        label = chosen.scanner.name or source
        _LOGGER.debug(
            "[%s] routed via proxy %s (rssi %s); load=%s",
            mac_u, label, chosen.advertisement.rssi, load,
        )
        return chosen.ble_device, label, chosen.advertisement.rssi
