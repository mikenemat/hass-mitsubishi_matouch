# Changelog

This is a hardened fork of [cyaneous/hass-mitsubishi_matouch](https://github.com/cyaneous/hass-mitsubishi_matouch),
focused on running several MA Touch (PAR-CT01MAU) thermostats reliably over ESP32
Bluetooth proxies, 24/7.

## 0.14.7

- **Fix room (current) temperature reading ~1°F high.** `current_temperature` was
  converted from the device's native °C with a round-**half-up** linear formula
  (`int(c*9/5+32+0.5)`), but the physical controller **truncates** the conversion (it
  drops the fraction; it does not round to nearest). In the common comfort band
  (~70-73°F) that made the HA card read **1°F above the wall display** — e.g. a sensor
  at 22.5°C (=72.5°F) showed **73** on the card but **72** on the thermostat, while
  23.0°C matched at 73. This was a *separate* path from the v0.13.3 setpoint fix
  (setpoints already used the Mitsubishi table and were correct). Room temp now
  truncates to match the controller exactly — **verified live against every unit**
  (decoded 0.5°C sensor value vs the physical wall display: 21.0→69, 22.0→71,
  22.5→72, 23.0→73). **°C systems stay exact:** the fix lives only in the °F branch;
  °C mode passes the device's native 0.5°C reading straight through — no conversion, so
  no rounding rule that could diverge from the controller.

## 0.14.6

- **Repairs notice for a wedged thermostat ("reachable but won't connect").** A unit
  can get into a state where its BLE radio keeps advertising (proxies see it, it shows
  multiple connection paths) but every connect/poll fails — so it goes `unavailable`
  and silently stays there for hours until someone notices and power-cycles it. The
  integration can't reset the unit's radio, but it can now *tell you*: when a unit is
  discoverable yet unjoinable for >10 min **while the rest of the fleet is healthy**, a
  Home Assistant *Repairs* issue is raised naming the unit and recommending a power
  cycle. It clears automatically the moment the unit reconnects. The detection is
  carefully scoped so it never fires for the benign cases — an offline/out-of-range
  unit (not discoverable), a wrong PIN or rejected command (the device answered, so the
  link works), or an ordinary periodic drop that recovers well under the threshold. The
  fleet-health gate keeps a global proxy outage from mis-blaming a single thermostat.

## 0.14.5

- **Fix: units still wouldn't grey on a sustained outage (follow-up to 0.14.4).** The
  bounded poll and the `is_stale` backstop were both working, but HA's
  `DataUpdateCoordinator` only notifies entities on a *success* or the *first* failure
  of a streak — so a card's availability was evaluated once at the start of the outage
  (while `is_stale` was not yet true) and **never re-checked**, leaving it "online" with
  stale data. (A unit only greyed if something else happened to nudge it — e.g. a
  setpoint-change revert.) Added a periodic availability tick that re-evaluates a
  *failing* unit every 15 s, so it greys within ~45–60 s of a real outage regardless of
  HA's listener behavior. No-op when healthy.

## 0.14.4

- **Fix: a unit could stay "online" through a proxy outage (and was slow to grey
  otherwise).** The per-poll timeout covered only the *connect* step, so a status read
  on a stale-but-"connected" link (proxy yanked, TCP not yet detected dead) could hang
  with no deadline — freezing the consecutive-failure counter so the card never went
  unavailable, and any optimistic setpoint change persisted indefinitely. Now:
  - the **entire** poll (connect + control writes + status read) runs under one
    timeout, so a hung step fails the poll and drops the link to reconnect;
  - availability has a **wall-clock staleness backstop** (no successful poll in several
    cadences ⇒ unavailable) that a frozen counter can't defeat;
  - a timed-out poll drops the stale link instead of trusting `is_connected`.

  A real outage is now detected in tens of seconds instead of minutes-or-never;
  recovery when the proxy returns is unchanged (fast).

## 0.14.3

- **Fix reload leaving every unit unavailable.** Setup blocked on a sequential
  per-thermostat BLE connect + firmware read (`async_config_entry_first_refresh`). On a
  **Reload**, the previous links are torn down first, so an immediate reconnect could
  hang and the GATT read got cancelled — and because it was the blocking first refresh,
  that cancelled the **whole** config entry with no retry, leaving all thermostats
  unavailable until a full restart. Setup now completes immediately and runs each unit's
  first poll in the background; a slow/flaky unit retries on its own backoff without
  taking down its siblings or the entry. (Startup was never affected — only reload.)

## 0.14.2

- **Fix raw translation placeholder in the config flow.** The "already configured"
  abort messages referenced Home Assistant's core string via
  `[%key:common::config_flow::abort::already_configured_device%]`, which only resolves
  for core integrations — so a custom install leaked the literal placeholder text into
  the dialog. Replaced with plain messages (parent flow points you to "Add thermostat"
  on the existing entry; subentry flow says the thermostat is already added).

## 0.14.1

- **Fix migration duplicate-device (regression in 0.14.0).** When a pre-fork entry
  was *promoted* in place to become the parent, its device kept a stray entry-level
  link alongside the new subentry link, so it showed up twice — once under its
  thermostat and once under "Devices that don't belong to a sub-entry." The promote
  path now drops that stray link (matching Home Assistant's own
  `openai_conversation` migration). Instances already migrated by 0.14.0 **self-heal**
  on upgrade (a one-time pass removes the stray link — no re-pairing needed).

## 0.14.0

- **Upgrade migration from the upstream (cyaneous) integration.** The original
  integration created one config entry per thermostat; this fork uses a single
  parent entry with a subentry per thermostat. Upgrading in place previously left
  the old pairings stranded and could create duplicate devices. A config-entry
  migration (minor version → 2, so reverting stays clean) now **adopts existing
  pairings**: it folds each old
  per-thermostat entry into the parent as a subentry and re-homes the existing
  device + climate entity, so **entity IDs and history are preserved** (no
  re-pairing, no duplicates). Early-fork parent entries are version-bumped in place
  (no structural change). The new diagnostic sensors appear fresh (expected).

## 0.13.9

- **Response checksum validation.** Every reply now has its trailing checksum
  verified (`sum(all bytes before the 2-byte checksum)` as a 16-bit little-endian
  value — confirmed against raw frames captured live from all five units). A
  mismatch is treated as a corrupt frame and retried, instead of being trusted.
  This eliminates rare **spurious "incorrect PIN"** events (a reply corrupted in
  transit whose result byte happened to read `0x02` was being misread as a wrong
  PIN) and guards against silently-corrupted temperature/mode data. Wire spec is
  regression-locked in `tests/test_checksum.py`.

## 0.13.4 – 0.13.8

- **Command confirmation.** Setpoint/mode/fan changes apply optimistically, then
  re-read; if the device rejects or the link drops, the entity reverts to actual
  state and surfaces a clear error — so the card never shows a change that didn't
  take.
- **Availability grace.** A single failed poll no longer flips the card to
  "unavailable"; it takes a short streak, which rides out one-off BLE hiccups.
- **Invalid-PIN repair.** A genuinely wrong PIN now raises a Home Assistant
  *Repairs* issue pointing at Reconfigure, and only after **3 consecutive** real
  rejections — connectivity blips can't false-trigger it.

## 0.13.3

- **Fahrenheit fix (upstream issue #4).** The device is Celsius-native (0.5°C steps)
  and Mitsubishi's °F display uses a non-linear lookup table, so reporting °C and
  letting HA convert produced a ~1°F off-by-one vs the physical controller. The
  climate entity now presents **Fahrenheit natively** (when HA's unit system is °F)
  using Mitsubishi's exact "standard" table, so HA does no conversion and the card
  matches the controller. Celsius systems are unchanged (exact passthrough).
  Conversion lives in `temperature.py`; round-trips are covered by `tests/`.
  - **Upgrade note for °F users:** the entity's reported unit changes from °C to °F,
    so `current_temperature` history shows a one-time discontinuity and HA may raise
    a "units changed" repair (resolvable via Developer Tools → Statistics). °C users
    are unaffected.
  - Known limitation: in °F mode the settable range matches the controller's °F
    table (61–88 °F = 16.0–30.5 °C); 31.0 °C is only reachable in °C mode.
- Config flow now **auto-searches** with a live progress spinner that advances as
  soon as a unit appears, instead of a dead-end "no devices found" abort.
- Resilience hardening: per-device exponential backoff with jitter on connect/link
  failures, reachability-aware proxy rebalancing (single-flight + debounced),
  immediate recovery when a proxy returns, and assorted concurrency fixes.

## 0.7.0 – 0.13.x (fork additions vs upstream 0.6.x)

- **Persistent BLE connections** with a 10s keepalive read that defeats the device's
  ~16s idle disconnect (per-poll cost drops to a single status round-trip).
- **ESP32 Bluetooth-proxy load balancing** across multiple proxies (least-loaded
  reachable proxy per device), so 4–5 thermostats share the radios evenly.
- **Telemetry** for R&D: a `mitsubishi_matouch.get_telemetry` service (response data),
  diagnostic sensors (uptime, reconnects, latency, active proxy, signal), Download
  Diagnostics, and an optional JSONL log. Raw-frame capture is opt-in (default off)
  to stay sustainable for 24/7 operation.
- **Config subentries:** one parent entry plus a removable "thermostat" subentry per
  device — scan-and-multi-select to add several at once, add/remove later without
  resetting the others.
- Pull-model climate entity, address-first setup (tolerates a unit briefly offline),
  and numerous reliability/QA fixes.
