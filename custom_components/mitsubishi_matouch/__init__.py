"""Support for Mitsubishi MA Touch thermostats."""

import asyncio
import logging
import time
from datetime import timedelta
from types import MappingProxyType

import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.const import CONF_ADDRESS, CONF_NAME, CONF_PIN, Platform
from homeassistant.config_entries import ConfigSubentry
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, format_mac
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType
import homeassistant.helpers.config_validation as cv

from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    REBALANCE_COOLDOWN,
    REBALANCE_DEBOUNCE,
    REBALANCE_INTERVAL,
    REBALANCE_STEP_DELAY,
    SIGNAL_NEW_THERMOSTAT,
    SUBENTRY_TYPE_THERMOSTAT,
)
from .models import MAConfigEntry, MARuntimeData
from .coordinator import MACoordinator
from .proxy_balancer import MAProxyBalancer
from .telemetry import MATelemetryLog

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.CLIMATE,
    Platform.SENSOR,
]

SERVICE_GET_TELEMETRY = "get_telemetry"
GET_TELEMETRY_SCHEMA = vol.Schema(
    {
        vol.Optional("mac"): cv.string,
        vol.Optional("limit", default=300): vol.All(vol.Coerce(int), vol.Range(min=1, max=1000)),
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register integration-level services (telemetry export for R&D)."""

    async def _get_telemetry(call: ServiceCall) -> ServiceResponse:
        domain_data = hass.data.get(DOMAIN, {})
        telemetry = domain_data.get("telemetry")
        balancer = domain_data.get("balancer")
        if telemetry is None:
            return {"events": [], "proxy_assignments": {}, "telemetry_path": None}
        return {
            "events": telemetry.recent(call.data.get("mac"), limit=call.data["limit"]),
            "proxy_assignments": balancer.assignments if balancer else {},
            "telemetry_path": telemetry.path,
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_TELEMETRY,
        _get_telemetry,
        schema=GET_TELEMETRY_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    return True


def _shared(hass: HomeAssistant) -> tuple[MAProxyBalancer, MATelemetryLog]:
    """Get-or-create the shared balancer + telemetry log (one per HA instance)."""

    domain_data = hass.data.setdefault(DOMAIN, {})
    balancer = domain_data.get("balancer")
    if balancer is None:
        balancer = MAProxyBalancer(hass)
        domain_data["balancer"] = balancer
    telemetry = domain_data.get("telemetry")
    if telemetry is None:
        telemetry = MATelemetryLog(hass)
        domain_data["telemetry"] = telemetry
    return balancer, telemetry


def _build_coordinator(hass: HomeAssistant, entry: MAConfigEntry, subentry: ConfigSubentry) -> MACoordinator:
    """Construct (but don't start) a coordinator for one thermostat subentry."""

    balancer, telemetry = _shared(hass)
    address = subentry.data[CONF_ADDRESS]
    pin = subentry.data[CONF_PIN]
    name = subentry.data.get(CONF_NAME) or f"MA Touch {address}"
    device = bluetooth.async_ble_device_from_address(hass, address.upper(), connectable=True)
    return MACoordinator(
        hass,
        config_entry=entry,
        pin=pin,
        scan_interval=entry.options.get("scan_interval", DEFAULT_SCAN_INTERVAL),
        address=address,
        ble_device=device,
        balancer=balancer,
        telemetry=telemetry,
        name=name,
        log_polls=entry.options.get("log_polls", False),
        capture_raw_frames=entry.options.get("capture_raw_frames", False),
    )


async def _first_refresh(coordinator: MACoordinator, address: str) -> None:
    """First refresh that tolerates a unit being briefly unreachable at startup."""

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        _LOGGER.warning("MA Touch '%s' not ready yet; it will retry", address)


# HA runs async_migrate_entry once per stored entry, concurrently. This lock
# serializes the "find-or-create the single parent" step so two legacy per-device
# entries can't each elect a separate parent.
_MIGRATION_LOCK = asyncio.Lock()


def _legacy_mac(entry: MAConfigEntry) -> str | None:
    """Extract a thermostat MAC from a pre-fork (per-device) config entry.

    Upstream stored it as data['mac_address'] (user flow) or only in the entry's
    unique_id (bluetooth flow). Returns a normalized MAC, or None if unrecognizable.
    """

    raw = entry.data.get("mac_address") or entry.unique_id
    return format_mac(raw) if raw else None


@callback
def _adopt_existing_records(
    hass: HomeAssistant, mac: str, parent: MAConfigEntry, subentry_id: str
) -> None:
    """Re-home the pre-existing device + climate entity for `mac` onto parent+subentry.

    The bluetooth connection key {(bluetooth, mac)} and the climate unique_id
    `matouch_<mac>` are byte-identical across upstream and this fork, so re-pointing
    the existing registry rows preserves the device, the entity_id, and recorder
    history instead of spawning duplicates. Missing rows are simply skipped — fresh
    setup will create them (e.g. the new diagnostic sensors, which upstream lacked).
    """

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    device = device_registry.async_get_device(
        connections={(CONNECTION_BLUETOOTH, format_mac(mac))}
    )
    if device is not None:
        device_registry.async_update_device(
            device.id,
            add_config_entry_id=parent.entry_id,
            add_config_subentry_id=subentry_id,
        )

    entity_id = entity_registry.async_get_entity_id("climate", DOMAIN, f"matouch_{format_mac(mac)}")
    if entity_id is not None:
        updates: dict = {"config_entry_id": parent.entry_id, "config_subentry_id": subentry_id}
        if device is not None:
            updates["device_id"] = device.id
        entity_registry.async_update_entity(entity_id, **updates)


def _ensure_subentry(
    hass: HomeAssistant,
    parent: MAConfigEntry,
    mac: str,
    address: str,
    name: str,
    pin: str | None,
) -> None:
    """Idempotently represent one thermostat as a subentry on `parent` and re-home
    its existing device + climate entity. Safe to call repeatedly (skips the add when
    a subentry for this MAC already exists), so concurrent/re-run migrations converge.
    """

    existing = next(
        (
            se
            for se in parent.subentries.values()
            if se.subentry_type == SUBENTRY_TYPE_THERMOSTAT
            and se.unique_id
            and format_mac(se.unique_id) == mac
        ),
        None,
    )
    if existing is not None:
        subentry_id = existing.subentry_id
    else:
        subentry = ConfigSubentry(
            data=MappingProxyType({CONF_ADDRESS: address, CONF_NAME: name, CONF_PIN: pin}),
            subentry_type=SUBENTRY_TYPE_THERMOSTAT,
            title=name,
            unique_id=address,
        )
        hass.config_entries.async_add_subentry(parent, subentry)
        subentry_id = subentry.subentry_id

    _adopt_existing_records(hass, mac, parent, subentry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: MAConfigEntry) -> bool:
    """Adopt pre-fork config entries into the parent + per-thermostat-subentry model.

    Upstream (cyaneous <=0.6.x) created ONE config entry per thermostat
    (unique_id = MAC, data = {mac_address, pin}). This fork uses ONE parent entry
    (unique_id = DOMAIN) with a 'thermostat' subentry per device. Without this hook
    those old entries load empty and bluetooth discovery mints duplicate devices.

    Strategy (serialized so concurrent per-entry calls don't each make a parent):
      - The first legacy entry is PROMOTED in place to become the single parent.
      - Every other legacy entry FOLDS its thermostat into that parent as a subentry,
        then removes itself (deferred — we're mid-migration of that very entry).
      - The existing device + climate entity are re-homed to the subentry so
        entity_id and history survive.

    Early-fork parent entries (unique_id == DOMAIN) predate this VERSION and only
    need a version bump — they are already the right shape.
    """

    # Already the parent shape (a real v2 install or an early-fork v1 parent): just
    # ensure the version is current so this doesn't re-run every start.
    if entry.unique_id == DOMAIN:
        if entry.version != 2:
            hass.config_entries.async_update_entry(entry, version=2)
        return True

    mac = _legacy_mac(entry)
    if mac is None:
        _LOGGER.error("Cannot migrate MA Touch entry %s: no MAC in data/unique_id", entry.entry_id)
        return False

    pin = entry.data.get(CONF_PIN) or entry.data.get("pin")
    name = entry.title or f"MA Touch {mac}"
    # The fork stores the canonical HA BLE address (uppercase) in subentry data; the
    # registry rows key off format_mac (lowercase). Keep both straight: `address`
    # for subentry data/unique_id, `format_mac(mac)` for device/entity adoption.
    address = mac.upper()

    async with _MIGRATION_LOCK:
        parent = next(
            (e for e in hass.config_entries.async_entries(DOMAIN) if e.unique_id == DOMAIN),
            None,
        )
        promoting = parent is None
        if promoting:
            # Promote THIS entry in place to be the single parent.
            hass.config_entries.async_update_entry(
                entry, unique_id=DOMAIN, title="Mitsubishi MA Touch", data={}, version=2
            )
            parent = entry

        # Adopt this entry's own thermostat (using values captured BEFORE the promote
        # above wiped this entry's data).
        _ensure_subentry(hass, parent, mac, address, name, pin)

        # If we just created the parent, its async_setup_entry is about to run and must
        # see EVERY subentry. So fold all other still-legacy entries up front now,
        # before any setup enumerates — this makes the upstream->fork path race-free
        # regardless of HA's entry-setup concurrency. Idempotent: each sibling also
        # self-confirms and removes itself when its own migration runs.
        if promoting:
            for other in hass.config_entries.async_entries(DOMAIN):
                if other.entry_id == entry.entry_id or other.unique_id == DOMAIN:
                    continue
                other_mac = _legacy_mac(other)
                if other_mac is None:
                    continue
                _ensure_subentry(
                    hass,
                    parent,
                    other_mac,
                    other_mac.upper(),
                    other.title or f"MA Touch {other_mac}",
                    other.data.get(CONF_PIN) or other.data.get("pin"),
                )

        if parent is not entry:
            # This legacy per-device entry is now folded into the parent; drop it.
            # Deferred (not awaited) because we're inside this entry's own migration;
            # device/entity are already re-homed, so nothing is orphaned. Left at
            # version 1 so an interrupted removal just re-folds idempotently next start.
            _LOGGER.info("Folded MA Touch %s into the parent entry; removing legacy entry", mac)
            hass.async_create_task(hass.config_entries.async_remove(entry.entry_id))

    return True


async def async_setup_entry(hass: HomeAssistant, entry: MAConfigEntry) -> bool:
    """Set up the parent entry: one coordinator per thermostat subentry."""

    _shared(hass)
    runtime = MARuntimeData(options=dict(entry.options))
    entry.runtime_data = runtime

    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_THERMOSTAT:
            continue
        coordinator = _build_coordinator(hass, entry, subentry)
        await _first_refresh(coordinator, subentry.data[CONF_ADDRESS])
        runtime.coordinators[subentry.subentry_id] = coordinator
        runtime.subentry_data[subentry.subentry_id] = dict(subentry.data)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_update_listener))

    # Active rebalance. A single Debouncer single-flights + coalesces all rebalance
    # triggers (proxy online/offline events + a slow periodic backstop) so bursty
    # proxy flap can't spawn overlapping bounce storms. The function runs one
    # bounded sweep per call. NOTE: Debouncer classifies its `function` via
    # iscoroutinefunction, so it must be a real `async def` — a lambda returning a
    # coroutine would be run in the executor and the coroutine never awaited.
    async def _do_rebalance() -> None:
        await _rebalance(hass, entry)

    runtime.rebalancer = Debouncer(
        hass,
        _LOGGER,
        cooldown=REBALANCE_DEBOUNCE,
        immediate=False,
        function=_do_rebalance,
    )

    async def _periodic_rebalance(_now) -> None:
        if entry.runtime_data and entry.runtime_data.rebalancer:
            await entry.runtime_data.rebalancer.async_call()

    entry.async_on_unload(
        async_track_time_interval(hass, _periodic_rebalance, timedelta(seconds=REBALANCE_INTERVAL))
    )
    unsub_proxy = _register_proxy_events(hass, entry)
    if unsub_proxy is not None:
        entry.async_on_unload(unsub_proxy)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: MAConfigEntry) -> bool:
    """Unload the parent entry and close all BLE connections."""

    # Stop the rebalancer first so no sweep is mid-flight while we tear down.
    # async_shutdown() is a synchronous @callback — do NOT await it.
    if entry.runtime_data.rebalancer is not None:
        entry.runtime_data.rebalancer.async_shutdown()
        entry.runtime_data.rebalancer = None

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    # Close links + release balancer assignments even if a platform refused to
    # unload, so we never leak BLE connections / proxy slots across a reload.
    for coordinator in entry.runtime_data.coordinators.values():
        await coordinator.async_close_connection()
    return unload_ok


async def _start_subentry(hass: HomeAssistant, entry: MAConfigEntry, subentry: ConfigSubentry) -> None:
    """Bring up a newly added thermostat subentry WITHOUT reloading siblings."""

    coordinator = _build_coordinator(hass, entry, subentry)
    entry.runtime_data.coordinators[subentry.subentry_id] = coordinator
    entry.runtime_data.subentry_data[subentry.subentry_id] = dict(subentry.data)
    # IMPORTANT: the parent entry is already LOADED here, so we must NOT call
    # async_config_entry_first_refresh() (it asserts SETUP_IN_PROGRESS and raises
    # RuntimeError, which would abort before the dispatch below and leave the new
    # thermostat with no entities). async_refresh() never raises; the device just
    # shows unavailable until it's reachable.
    await coordinator.async_refresh()
    # Tell the already-loaded platforms to create this subentry's entities.
    async_dispatcher_send(hass, f"{SIGNAL_NEW_THERMOSTAT}_{entry.entry_id}", subentry.subentry_id)


async def _stop_subentry(hass: HomeAssistant, entry: MAConfigEntry, subentry_id: str) -> None:
    """Tear down a removed thermostat subentry. The framework auto-removes its
    entities + device; we only stop the coordinator and close the BLE link."""

    entry.runtime_data.subentry_data.pop(subentry_id, None)
    coordinator = entry.runtime_data.coordinators.pop(subentry_id, None)
    if coordinator is not None:
        coordinator.clear_auth_issue()
        await coordinator.async_shutdown()
        await coordinator.async_close_connection()


async def _update_listener(hass: HomeAssistant, entry: MAConfigEntry) -> None:
    """React to subentry/option changes.

    Add/remove of a thermostat is handled incrementally so siblings keep their
    connections. Options (scan interval / logging) apply live. Only a reconfigure
    (PIN/name change) falls back to a parent reload, since it needs a fresh link.
    """

    runtime = entry.runtime_data
    # HA does not serialize listener invocations; a rapid add+remove (or double
    # add) would otherwise diff against a snapshot another invocation is mutating,
    # orphaning a coordinator or creating no entities. Take the snapshot + apply
    # the diff atomically under the runtime lock.
    async with runtime.update_lock:
        # A listener invocation that queued on the lock before a reload would now
        # be operating against a replaced runtime — bail rather than diff stale state.
        if entry.runtime_data is not runtime:
            return
        current = {
            sid: se
            for sid, se in entry.subentries.items()
            if se.subentry_type == SUBENTRY_TYPE_THERMOSTAT
        }
        current_ids = set(current)
        known_ids = set(runtime.coordinators)

        # A PIN/name reconfigure needs a fresh authenticated link, so reload.
        reconfigured = any(
            dict(current[sid].data) != runtime.subentry_data.get(sid)
            for sid in (current_ids & known_ids)
        )
        if reconfigured:
            await hass.config_entries.async_reload(entry.entry_id)
            return

        # Options (scan interval / verbose / raw-frame capture) apply live — no
        # reload, so siblings keep their connections. Update the snapshot in
        # lockstep so the next diff baselines correctly.
        if dict(entry.options) != runtime.options:
            opts = entry.options
            for coordinator in runtime.coordinators.values():
                coordinator.apply_options(
                    opts.get("scan_interval", DEFAULT_SCAN_INTERVAL),
                    opts.get("log_polls", False),
                    opts.get("capture_raw_frames", False),
                )
            runtime.options = dict(entry.options)

        for sid in known_ids - current_ids:
            await _stop_subentry(hass, entry, sid)
        for sid in current_ids - known_ids:
            await _start_subentry(hass, entry, current[sid])


def _register_proxy_events(hass: HomeAssistant, entry: MAConfigEntry):
    """Best-effort: react when an ESP32 proxy goes online/offline.

    Uses the habluetooth manager's scanner-registration callback (not part of core's
    public API), guarded so a version change just falls back to the periodic timer.

    On a proxy ADDED (capacity returned): wake every coordinator so any backed-off /
    unavailable unit retries and recovers ASAP, clear the rebalance cooldowns (the
    topology improved, re-evaluate freely), then schedule a rebalance. On REMOVED:
    prune the dead proxy's slots from the load map, then schedule a rebalance.
    """

    try:
        from habluetooth import get_manager, HaScannerRegistrationEvent

        manager = get_manager()
    except Exception as ex:  # noqa: BLE001
        _LOGGER.debug("proxy registration events unavailable, using periodic rebalance: %s", ex)
        return None

    @callback
    def _on_proxy(registration) -> None:
        runtime = entry.runtime_data
        if runtime is None:
            return
        event = getattr(registration, "event", None)
        if event == HaScannerRegistrationEvent.ADDED:
            runtime.rebalance_cooldown.clear()
            for coordinator in runtime.coordinators.values():
                coordinator.request_immediate_retry()
        elif event == HaScannerRegistrationEvent.REMOVED:
            scanner = getattr(registration, "scanner", None)
            source = getattr(scanner, "source", None)
            balancer = hass.data.get(DOMAIN, {}).get("balancer")
            if source and balancer is not None:
                balancer.prune_source(source)
        else:
            return
        if runtime.rebalancer is not None:
            hass.async_create_task(runtime.rebalancer.async_call())

    try:
        return manager.async_register_scanner_registration_callback(_on_proxy, None)
    except Exception as ex:  # noqa: BLE001
        _LOGGER.debug("could not register proxy events: %s", ex)
        return None


async def _rebalance(hass: HomeAssistant, entry: MAConfigEntry) -> None:
    """Spread connections toward an even per-proxy share.

    Runs a bounded sweep: each step bounces at most one device off the most-loaded
    proxy (it then re-picks the least-loaded reachable proxy), waits for it to land,
    and re-evaluates. Terminates when balanced or when every over-share device is in
    cooldown, so it can't loop forever even if a device can only reach one proxy
    (that device returns to the same proxy, gets cooled, and is skipped). Serialized
    + coalesced by the Debouncer that invokes it.
    """

    runtime = entry.runtime_data
    if runtime is None or hass.is_stopping or runtime.rebalancing:
        return
    runtime.rebalancing = True
    try:
        # Bounded by the device count: each device is bounced at most once per sweep
        # (cooldown is far longer than a sweep), so the loop always drains.
        for _ in range(len(runtime.coordinators) + 1):
            if runtime is not entry.runtime_data or hass.is_stopping:
                return
            if not await _rebalance_one(hass, entry):
                return
            await asyncio.sleep(REBALANCE_STEP_DELAY)
    except Exception as ex:  # noqa: BLE001 - rebalance must never break the integration
        _LOGGER.debug("rebalance error: %s", ex)
    finally:
        runtime.rebalancing = False


async def _rebalance_one(hass: HomeAssistant, entry: MAConfigEntry) -> bool:
    """One rebalance step. Returns True if a device was bounced (sweep continues)."""

    runtime = entry.runtime_data
    if runtime is None:
        return False
    connected = [c for c in runtime.coordinators.values() if c.connected and c.active_source]
    if len(connected) < 2:
        return False

    try:
        scanners = bluetooth.async_current_scanners(hass)
    except Exception:  # noqa: BLE001
        return False
    # Treat a scanner with no 'connectable' attribute as NOT connectable, so a
    # non-connectable scanner can't inflate the proxy count and force futile moves.
    sources = {s.source for s in scanners if getattr(s, "connectable", False)}
    if len(sources) < 2:
        return False  # nothing to spread across

    load: dict[str, int] = {}
    for coordinator in connected:
        load[coordinator.active_source] = load.get(coordinator.active_source, 0) + 1

    optimal = -(-len(connected) // len(sources))  # ceil(devices / proxies)
    most = max(load, key=load.get)
    if load[most] <= optimal:
        return False  # already balanced

    now = time.monotonic()
    for coordinator in connected:
        if coordinator.active_source != most:
            continue
        if now - runtime.rebalance_cooldown.get(coordinator.mac_address, 0.0) < REBALANCE_COOLDOWN:
            continue
        runtime.rebalance_cooldown[coordinator.mac_address] = now
        _LOGGER.info(
            "Rebalancing %s off proxy %s (load %s > even share %s)",
            coordinator.mac_address, most, load[most], optimal,
        )
        await coordinator.async_rebalance()
        return True
    return False
