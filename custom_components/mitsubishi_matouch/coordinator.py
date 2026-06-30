"""Data update coordinator for Mitsubishi MA Touch thermostats."""

import logging
import random
import time
import asyncio
from datetime import timedelta
from dataclasses import replace

from homeassistant.core import HomeAssistant, callback
from homeassistant.components import bluetooth
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed, ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.exceptions import HomeAssistantError

from bleak.backends.device import BLEDevice

from .btmatouch.const import MAOperationMode, MAFanMode, MAVaneMode
from .btmatouch.thermostat import Status, Thermostat
from .btmatouch.exceptions import MAException, MAAuthException, MAControlRequestFailedException

from .models import MAConfigEntry
from .proxy_balancer import MAProxyBalancer
from .telemetry import MATelemetryLog
from .const import DOMAIN, MAX_BACKOFF_INTERVAL, WEDGED_UNIT_THRESHOLD

_LOGGER = logging.getLogger(__name__)

# Hard ceiling on ONE full poll — connect + login + queued control writes + status
# read — wrapping the WHOLE poll, not just the connect. A stale-but-"connected" link
# (proxy yanked, TCP not yet detected dead) can otherwise hang the status read / GATT
# lock with no deadline, which freezes the failure counter and leaves the unit looking
# online indefinitely. On expiry the poll fails and the link is dropped to reconnect.
_POLL_TIMEOUT = 30
# Wall-clock backstop: grey the card if no poll has SUCCEEDED in this long (scaled to
# the poll cadence). Unlike the consecutive-failure counter, a hung poll can't freeze
# the clock — this is what guarantees a unit can't stay "online" through an outage.
_STALE_FLOOR = 45
# Minimum seconds between persisting repeated identical poll failures to the file.
_FAILURE_LOG_INTERVAL = 60
# Consecutive bad-PIN responses required before raising the Repairs issue. The
# controller is observed to return a spurious one-off BAD_PIN with the CORRECT PIN
# (the inbound response checksum isn't validated yet, so a corrupted reply whose
# result byte reads 0x02 is misread as a wrong PIN). Require several in a row, reset
# on any success — a real wrong PIN still flags in ~3 polls; isolated flukes never do.
_AUTH_FAIL_THRESHOLD = 3


class MACoordinator(DataUpdateCoordinator):
    """Mitsubishi MA Touch data update coordinator."""

    def __init__(self, hass: HomeAssistant, config_entry: MAConfigEntry, pin: str, scan_interval: int, address: str, ble_device: BLEDevice | None, balancer: MAProxyBalancer, telemetry: MATelemetryLog, name: str, log_polls: bool = False, capture_raw_frames: bool = False):
        """Initialize the coordinator."""

        super().__init__(
            hass,
            _LOGGER,
            # Name of the data. For logging purposes.
            name=address,
            config_entry=config_entry,
            # Polling interval. Will only be polled if there are subscribers.
            update_interval=timedelta(seconds=scan_interval),
            # Dispatch on every poll so the diagnostic/telemetry sensors (uptime,
            # latency, active proxy, ...) refresh even when the thermostat Status
            # itself is unchanged. (Setting False left climate stuck "unknown".)
            always_update=True,
        )

        self._mac_address = address
        self.device_name = name
        self._balancer = balancer
        self._telemetry = telemetry
        self._log_polls = log_polls
        self._capture_raw_frames = capture_raw_frames
        # Base poll cadence + exponential-backoff state (so an unreachable unit or
        # a saturated proxy is not hammered every interval during an outage).
        self._base_interval = scan_interval
        self._fail_streak = 0
        # Monotonic time of the last SUCCESSFUL poll, for the staleness backstop
        # (None until the first success).
        self._last_success_monotonic: float | None = None
        # Set by async_rebalance(); honored at the top of the next (serialized)
        # poll so the re-pick happens under the coordinator's refresh lock.
        self._force_repick = False
        # Repairs issue for a rejected PIN: raised on an auth failure, cleared on
        # the next successful poll or when the thermostat is removed.
        self._issue_id = f"invalid_pin_{address}"
        self._auth_issue_active = False
        self._auth_fail_streak = 0
        # "Wedged radio" Repairs issue: the unit is DISCOVERABLE (a proxy sees it
        # advertising) but every connect/poll keeps failing. _discoverable_fail_since
        # marks when that streak began (None whenever the unit is offline, the link
        # works, or a poll succeeds); is_wedged trips once it exceeds the threshold.
        self._wedged_issue_id = f"wedged_{address}"
        self._wedged_issue_active = False
        self._discoverable_fail_since: float | None = None
        self._prev_connect_count = 0
        self._prev_disconnect_count = 0
        self._prev_status_hex: str | None = None
        self._last_fail_persist: float | None = None
        self._last_fail_error: str | None = None
        self.active_proxy: str | None = None
        self._active_source: str | None = None
        self.active_rssi: int | None = None
        self.last_poll_duration: float | None = None
        self._thermostat = Thermostat(
            pin=int(pin, 16),
            address=address,
            ble_device=ble_device,
        )
        self._target_heat_setpoint: float | None = None
        self._target_cool_setpoint: float | None = None
        self._target_operation_mode: MAOperationMode | None = None
        self._target_fan_mode: MAFanMode | None = None
        self._target_vane_mode: MAVaneMode | None = None

    @property
    def firmware_version(self) ->  str | None:
        """Get the thermostat firmware version."""

        return self._thermostat.firmware_version

    @property
    def software_version(self) -> str | None:
        """Get the thermostat software version."""

        return self._thermostat.software_version

    @property
    def login_responses(self) -> dict[str, str]:
        """Raw login/begin-session response hex, surfaced in diagnostics so the
        device-info / capability frame layout can be reverse-engineered from real
        bytes before a parser is written."""

        return self._thermostat.last_login_responses

    @property
    def reconnects(self) -> int:
        """Reconnects since startup (successful connects after the first)."""

        return max(0, self._thermostat.connect_count - 1)

    @property
    def disconnects(self) -> int:
        """Disconnect events observed since startup."""

        return self._thermostat.disconnect_count

    @property
    def connection_uptime(self) -> float | None:
        """Seconds the current BLE connection has been alive, or None if down."""

        return self._thermostat.connection_uptime

    @property
    def mac_address(self) -> str:
        """The thermostat's BLE MAC address."""

        return self._mac_address

    @property
    def active_source(self) -> str | None:
        """Proxy source (MAC) actually serving the live connection."""

        return self._active_source

    @property
    def connected(self) -> bool:
        """Whether the thermostat currently has a live BLE connection."""

        return self._thermostat.is_connected

    @property
    def consecutive_failures(self) -> int:
        """Number of back-to-back failed polls (0 once a poll succeeds)."""

        return self._fail_streak

    @property
    def is_stale(self) -> bool:
        """True if no poll has SUCCEEDED for several cadences — a wall-clock outage
        backstop. The consecutive-failure counter can be frozen by a hung in-flight
        poll (which never records a result); the clock cannot, so this is what
        guarantees a unit greys out even if its poll is wedged. Scales with the poll
        interval so a long cadence doesn't false-trip between polls.
        """

        if self._last_success_monotonic is None:
            return True
        threshold = max(self._base_interval * 4, _STALE_FLOOR)
        return (time.monotonic() - self._last_success_monotonic) > threshold

    @property
    def is_wedged(self) -> bool:
        """True when the thermostat is DISCOVERABLE (a proxy sees it advertising) yet
        every connect/poll has failed for longer than WEDGED_UNIT_THRESHOLD — i.e. its
        BLE radio is wedged and almost always needs a power cycle (something the
        integration cannot do for it).

        Deliberately distinct from the other failure modes so the Repairs notice only
        fires on the one the user can actually act on:
          - 'offline' (not discoverable) leaves _discoverable_fail_since None;
          - a wrong PIN ('auth') or a rejected control ('control') means the link
            actually works (the device answered), so it isn't wedged;
          - an ordinary periodic drop recovers (success) long before the threshold.
        """

        if self._discoverable_fail_since is None:
            return False
        return (time.monotonic() - self._discoverable_fail_since) > WEDGED_UNIT_THRESHOLD

    @callback
    def _clear_active_proxy(self) -> None:
        """Forget the serving proxy once the link is down so the diagnostic
        sensors don't report a stale dead proxy through an outage (R10)."""

        self._active_source = None
        self.active_proxy = None
        self.active_rssi = None

    @callback
    def _apply_backoff(self) -> None:
        """Grow the poll interval on repeated failures (exp backoff + jitter)."""

        self._fail_streak += 1
        exp = min(self._fail_streak - 1, 4)
        delay = min(self._base_interval * (2 ** exp), MAX_BACKOFF_INTERVAL)
        # Jitter de-synchronizes the fleet so they don't all re-hit a returning
        # proxy on the same tick.
        delay += random.uniform(0, self._base_interval)
        self.update_interval = timedelta(seconds=delay)

    @callback
    def _reset_backoff(self) -> None:
        """Restore the base poll cadence after a success."""

        if self._fail_streak:
            self._fail_streak = 0
            self.update_interval = timedelta(seconds=self._base_interval)

    @callback
    def request_immediate_retry(self) -> None:
        """Reset backoff and poll now (used when a proxy comes back online so a
        backed-off device recovers ASAP instead of waiting out its backoff).

        Skips an auth-failed device: a returning proxy doesn't fix a bad PIN, so
        resetting its backoff would just resume hammering connect+login against a
        wrong PIN on every proxy flap (defeats the auth backoff).
        """

        if self._last_fail_error == "auth":
            return
        self._reset_backoff()
        self.hass.async_create_task(self.async_request_refresh())

    @callback
    def apply_options(self, scan_interval: int, log_polls: bool, capture_raw_frames: bool) -> None:
        """Apply changed integration options live, without dropping the link.

        Lets the user toggle verbose/raw-frame logging or the poll cadence (e.g.
        when capturing an error) without bouncing every thermostat's connection.
        """

        self._log_polls = log_polls
        self._capture_raw_frames = capture_raw_frames
        if scan_interval != self._base_interval:
            self._base_interval = scan_interval
            # Only retake the cadence immediately if we're not mid-backoff; a
            # backed-off device picks up the new base on its next success.
            if self._fail_streak == 0:
                self.update_interval = timedelta(seconds=scan_interval)

    @callback
    def _raise_auth_issue(self) -> None:
        """Surface a Repairs issue telling the user this thermostat's PIN is wrong
        and needs reconfiguring. Idempotent (skips if already raised)."""

        if self._auth_issue_active:
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="invalid_pin",
            translation_placeholders={"name": self.device_name},
        )
        self._auth_issue_active = True

    @callback
    def clear_auth_issue(self) -> None:
        """Remove the invalid-PIN Repairs issue (on a successful poll or on removal).
        Safe to call when none exists (no-op), so it also clears a stale issue left
        by a previous coordinator instance across a reload."""

        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
        self._auth_issue_active = False
        self._auth_fail_streak = 0

    @callback
    def raise_wedged_issue(self) -> None:
        """Surface a Repairs issue: this thermostat is reachable over Bluetooth but
        won't connect, so its radio is likely wedged and needs a power cycle. Driven
        by the availability tick (which gates it on the rest of the fleet being
        healthy). Idempotent (skips if already raised)."""

        if self._wedged_issue_active:
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._wedged_issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="wedged",
            translation_placeholders={"name": self.device_name},
        )
        self._wedged_issue_active = True

    @callback
    def clear_wedged_issue(self) -> None:
        """Remove the wedged-radio Repairs issue (on recovery or removal). Safe to
        call when none exists (no-op), so it also clears a stale issue left by a
        previous coordinator instance across a reload."""

        ir.async_delete_issue(self.hass, DOMAIN, self._wedged_issue_id)
        self._wedged_issue_active = False

    @callback
    def _note_connection_source(self) -> None:
        """Record the proxy actually serving the live link (advertisement-based
        lookups can't see a connected device) for accurate telemetry + balancing."""

        # Prefer the source the live link reports; if the backend doesn't expose
        # it (some scanners / mid-reconnect), fall back to the proxy pick() chose
        # so the device still counts in the load map instead of going invisible.
        source = self._thermostat.connected_source or self._balancer.assigned_source(self._mac_address)
        if not source:
            return
        self._active_source = source
        scanner = bluetooth.async_scanner_by_source(self.hass, source)
        name = getattr(scanner, "name", None)
        self.active_proxy = f"{name} ({source})" if name else source
        self._balancer.note_connected(self._mac_address, source)

    async def async_rebalance(self) -> None:
        """Request a proxy re-pick on the next poll.

        Routed through the coordinator's own refresh (flag + request_refresh)
        rather than closing the link out-of-band: the disconnect/re-pick runs at
        the top of the next poll, under the refresh lock, so it can't race an
        in-flight status read. (request_refresh may coalesce with a poll already
        due; the per-device cooldown in the caller bounds re-bounce regardless.)
        """

        self._force_repick = True
        await self.async_request_refresh()

    async def _async_setup(self) -> None:
        """Set up the coordinator

        This is the place to set up your coordinator,
        or to load data, that only needs to be loaded once.

        This method will be called automatically during
        coordinator.async_config_entry_first_refresh.
        """

    async def _run_poll(self) -> Status:
        """One full poll: (re)connect, flush any queued control writes, read status.

        Runs under the caller's _POLL_TIMEOUT so a hung step — e.g. a status read on a
        stale-but-"connected" link after its proxy is yanked, or a GATT-lock wait
        behind a wedged keepalive — can't block the poll indefinitely and freeze the
        failure counter (which is what left units looking online through a full outage).
        """

        await self._thermostat.async_ensure_connected()
        self._note_connection_source()

        # Process pending control updates over the live connection. Clear a queued
        # value only after a successful write so failures can retry.
        if (heat_setpoint := self._target_heat_setpoint) is not None:
            try:
                await self._thermostat.async_set_heat_setpoint(heat_setpoint)
                self._target_heat_setpoint = None
            except MAControlRequestFailedException:
                self._target_heat_setpoint = None
                raise

        if (cool_setpoint := self._target_cool_setpoint) is not None:
            try:
                await self._thermostat.async_set_cool_setpoint(cool_setpoint)
                self._target_cool_setpoint = None
            except MAControlRequestFailedException:
                self._target_cool_setpoint = None
                raise

        if (operation_mode := self._target_operation_mode) is not None:
            try:
                await self._thermostat.async_set_operation_mode(operation_mode)
                self._target_operation_mode = None
            except MAControlRequestFailedException:
                self._target_operation_mode = None
                raise

        if (fan_mode := self._target_fan_mode) is not None:
            try:
                await self._thermostat.async_set_fan_mode(fan_mode)
                self._target_fan_mode = None
            except MAControlRequestFailedException:
                self._target_fan_mode = None
                raise

        if (vane_mode := self._target_vane_mode) is not None:
            try:
                await self._thermostat.async_set_vane_mode(vane_mode)
                self._target_vane_mode = None
            except MAControlRequestFailedException:
                self._target_vane_mode = None
                raise

        return await self._thermostat.async_get_status()

    async def _async_update_data(self) -> Status:
        """Fetch the latest status over a persistent BLE connection.

        The connection is established once and reused across polls (see
        Thermostat.async_ensure_connected); on any link error we drop it so the
        next poll reconnects cleanly. This keeps per-poll cost to a single status
        round-trip, which is what allows several devices to share the radios.
        """

        # Rebalance was requested: drop the current link (under this refresh lock,
        # so no concurrent poll is mid-status-read) so the pick() below re-routes
        # us onto the least-loaded reachable proxy.
        if self._force_repick:
            self._force_repick = False
            if self._thermostat.is_connected:
                self._balancer.release(self._mac_address)
                await self._thermostat.async_close()
                self._clear_active_proxy()

        # Only (re)select a proxy when we actually need to connect. While the
        # persistent connection is up the device isn't advertising, so the
        # advertisement-based balancer can't see it - re-picking then would wipe
        # active_proxy/rssi and drop the balancer's load assignment. Never clobber
        # a known value with None.
        if not self._thermostat.is_connected:
            ble_device, proxy, rssi = self._balancer.pick(self._mac_address)
            if ble_device is not None:
                self._thermostat.set_ble_device(ble_device)
            if proxy is not None:
                self.active_proxy = proxy
            if rssi is not None:
                self.active_rssi = rssi
            if ble_device is None:
                # Not currently seen by any proxy: don't hammer establish_connection
                # on an absent/powered-off unit (which would waste airtime and
                # degrade the proxies for live units). Wait until it advertises,
                # and back off the cadence so a long outage isn't polled every tick.
                self._clear_active_proxy()
                await self._record_poll(False, error="not_discoverable")
                self._apply_backoff()
                raise UpdateFailed(f"{self._mac_address} is not currently discoverable")

        started = time.monotonic()
        try:
            async with asyncio.timeout(_POLL_TIMEOUT):
                status = await self._run_poll()
            self.last_poll_duration = time.monotonic() - started
            result = self._apply_pending_targets_to_status(status)
            self._reset_backoff()
            self.clear_auth_issue()
            self.clear_wedged_issue()
            await self._record_poll(
                True,
                room_temperature=status.room_temperature,
                operation_mode=str(status.operation_mode),
            )
            return result
        except MAAuthException as ex:
            # Wrong PIN: raise a Repairs issue naming this thermostat so the user
            # knows to reconfigure its PIN (there's no per-subentry reauth flow in
            # the subentry model). Back off so we don't churn reconnect+login
            # attempts against a bad PIN every tick.
            await self._thermostat.async_close()
            self._clear_active_proxy()
            self._clear_pending_targets()
            # Flag a bad PIN only after repeated DEVICE-CONFIRMED rejections (the
            # device returned a parsed bad-PIN result code, not a connectivity
            # failure — those raise other exceptions handled below). A single fluke
            # won't raise the Repairs issue.
            self._auth_fail_streak += 1
            if self._auth_fail_streak >= _AUTH_FAIL_THRESHOLD:
                self._raise_auth_issue()
            await self._record_poll(False, error="auth", detail=str(ex))
            self._apply_backoff()
            raise UpdateFailed(f"Authentication failed (check PIN): {ex}") from ex
        except MAControlRequestFailedException as ex:
            # Device rejected the control, but the link is healthy - keep it and
            # don't back off (this isn't a connectivity problem).
            await self._record_poll(False, error="control", detail=str(ex))
            raise UpdateFailed(f"Control request failed: {ex}") from ex
        except TimeoutError as ex:
            # The whole poll exceeded _POLL_TIMEOUT — a hung connect/login/control/
            # status read (e.g. a stale link after a yanked proxy, or a wedged GATT
            # lock). Drop the link so the next poll reconnects cleanly, and back off.
            # Fix #3: this is what stops trusting a stale is_connected — a hung poll
            # on a "connected" link now times out and forces a fresh reconnect.
            await self._thermostat.async_close()
            self._clear_active_proxy()
            self._clear_pending_targets()
            await self._record_poll(False, error="poll_timeout", detail=str(ex))
            self._apply_backoff()
            raise UpdateFailed(f"Poll timed out: {ex}") from ex
        except MAException as ex:
            # Any other protocol/link error: drop the connection so the next
            # poll reconnects cleanly, and back off the cadence during an outage.
            await self._thermostat.async_close()
            self._clear_active_proxy()
            self._clear_pending_targets()
            await self._record_poll(False, error="link", detail=str(ex))
            self._apply_backoff()
            raise UpdateFailed(f"Error communicating with thermostat: {ex}") from ex

    def _apply_optimistic_update(self, **changes) -> None:
        """Apply optimistic status changes to coordinator data."""

        previous = self.data
        if previous is None:
            return

        self.async_set_updated_data(replace(previous, **changes))

    def _apply_pending_targets_to_status(self, status: Status) -> Status:
        """Overlay queued control targets on fetched status to avoid UI bounce-back."""

        changes: dict[str, float | MAOperationMode | MAFanMode | MAVaneMode] = {}

        if self._target_heat_setpoint is not None:
            changes["heat_setpoint"] = self._target_heat_setpoint
        if self._target_cool_setpoint is not None:
            changes["cool_setpoint"] = self._target_cool_setpoint
        if self._target_operation_mode is not None:
            changes["operation_mode"] = self._target_operation_mode
        if self._target_fan_mode is not None:
            changes["fan_mode"] = self._target_fan_mode
        if self._target_vane_mode is not None:
            changes["vane_mode"] = self._target_vane_mode

        if not changes:
            return status

        return replace(status, **changes)

    def _clear_pending_targets(self) -> None:
        """Drop queued control targets (e.g. after a lost/failed connection) so a
        never-confirmed command stops overlaying phantom state on the entity."""

        self._target_heat_setpoint = None
        self._target_cool_setpoint = None
        self._target_operation_mode = None
        self._target_fan_mode = None
        self._target_vane_mode = None

    def _raise_command_error(self) -> None:
        """Raise a clear error after a control attempt that did NOT apply, so the
        user is told it didn't take (rather than the card silently showing the new
        value as if it stuck)."""

        exc = self.last_exception
        root = (exc.__cause__ or exc) if exc is not None else None
        if isinstance(root, MAControlRequestFailedException):
            # The device received the command and rejected it (e.g. it's in menus).
            raise root
        # The command could not be delivered at all (link down / timeout / bad PIN /
        # not currently reachable through any proxy).
        raise HomeAssistantError(
            f"Couldn't reach {self.device_name} to apply the change — it was not changed."
        )

    async def _async_apply_command(self, **optimistic) -> None:
        """Send a queued control change and confirm it actually applied.

        The caller has already set the pending target(s). We reflect the change
        optimistically for instant card feedback, then run an AWAITED refresh
        (async_refresh, NOT the debounced async_request_refresh) so the command's
        own poll has completed by the time we check the result — that's what lets us
        reliably tell the user whether it applied. On failure we revert the
        optimistic value (so the card never shows an un-applied change as done) and
        raise so Home Assistant surfaces the failure.
        """

        previous = self.data
        self._apply_optimistic_update(**optimistic)
        await self.async_refresh()
        if not self.last_update_success:
            if previous is not None:
                self.data = previous
                self.async_update_listeners()
            self._raise_command_error()

    async def async_set_heat_setpoint(self, temperature: float) -> None:
        """Sets the heat setpoint."""

        self._target_heat_setpoint = temperature
        await self._async_apply_command(heat_setpoint=temperature)

    async def async_set_cool_setpoint(self, temperature: float) -> None:
        """Sets the cool setpoint."""

        self._target_cool_setpoint = temperature
        await self._async_apply_command(cool_setpoint=temperature)

    async def async_set_operation_mode(self, operation_mode: MAOperationMode) -> None:
        """Sets the operation mode."""

        self._target_operation_mode = operation_mode
        await self._async_apply_command(operation_mode=operation_mode)

    async def async_set_fan_mode(self, fan_mode: MAFanMode) -> None:
        """Sets the fan mode."""

        self._target_fan_mode = fan_mode
        await self._async_apply_command(fan_mode=fan_mode)

    async def async_set_vane_mode(self, vane_mode: MAVaneMode) -> None:
        """Sets the vane mode."""

        self._target_vane_mode = vane_mode
        await self._async_apply_command(vane_mode=vane_mode)

    async def async_close_connection(self) -> None:
        """Disconnect the persistent BLE connection (called on unload)."""

        await self._thermostat.async_close()
        self._balancer.release(self._mac_address)
        self._clear_active_proxy()

    async def _record_poll(self, success: bool, **extra) -> None:
        """Emit telemetry for this poll plus any connect/disconnect transitions."""

        thermostat = self._thermostat

        if thermostat.connect_count != self._prev_connect_count:
            self._prev_connect_count = thermostat.connect_count
            await self._telemetry.record(
                "connect",
                self._mac_address,
                proxy=self.active_proxy,
                rssi=self.active_rssi,
                connect_count=thermostat.connect_count,
            )

        if thermostat.disconnect_count != self._prev_disconnect_count:
            self._prev_disconnect_count = thermostat.disconnect_count
            await self._telemetry.record(
                "disconnect",
                self._mac_address,
                disconnect_count=thermostat.disconnect_count,
                last_uptime_s=(
                    round(thermostat.last_disconnect_uptime)
                    if thermostat.last_disconnect_uptime is not None
                    else None
                ),
            )

        # Capture the full raw STATUS frame only when it changes AND only if raw
        # capture is enabled (off by default - keeps the file lean for 24/7).
        fields = dict(extra)
        if self._capture_raw_frames:
            status_hex = thermostat.last_status_hex
            if success and status_hex and status_hex != self._prev_status_hex:
                fields["status_hex"] = status_hex
                self._prev_status_hex = status_hex

        # Decide what reaches the JSONL file. The in-memory ring buffer keeps every
        # poll regardless. Successful polls are persisted only in verbose mode;
        # failures are persisted but identical repeats are coalesced (persist on
        # transition, then at most once per _FAILURE_LOG_INTERVAL) so a flapping
        # unit can't self-erase the file over a multi-hour storm.
        if success:
            self._last_success_monotonic = time.monotonic()
            self._last_fail_persist = None
            self._last_fail_error = None
            # Link is healthy again: clear the wedged-radio streak (see is_wedged).
            self._discoverable_fail_since = None
            persist = self._log_polls
        else:
            error = extra.get("error")
            now = time.monotonic()
            # Track the "discoverable but won't connect" streak that feeds is_wedged.
            # Only a connectivity failure on a unit we could actually reach ('link' /
            # 'poll_timeout') counts: 'not_discoverable' means it's offline, and
            # 'auth'/'control' mean the device answered (so the radio works) — all of
            # those reset the streak so the wedged notice never fires for them.
            if error in ("link", "poll_timeout"):
                if self._discoverable_fail_since is None:
                    self._discoverable_fail_since = now
            else:
                self._discoverable_fail_since = None
            persist = (
                error != self._last_fail_error
                or self._last_fail_persist is None
                or (now - self._last_fail_persist) >= _FAILURE_LOG_INTERVAL
            )
            if persist:
                self._last_fail_persist = now
                self._last_fail_error = error

        uptime = thermostat.connection_uptime
        await self._telemetry.record(
            "poll" if success else "poll_failed",
            self._mac_address,
            persist,
            ok=success,
            proxy=self.active_proxy,
            rssi=self.active_rssi,
            latency_ms=(
                round(self.last_poll_duration * 1000)
                if success and self.last_poll_duration is not None
                else None
            ),
            uptime_s=round(uptime) if uptime is not None else None,
            reconnects=max(0, thermostat.connect_count - 1),
            disconnects=thermostat.disconnect_count,
            **fields,
        )
