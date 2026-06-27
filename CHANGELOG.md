# Changelog

This is a hardened fork of [cyaneous/hass-mitsubishi_matouch](https://github.com/cyaneous/hass-mitsubishi_matouch),
focused on running several MA Touch (PAR-CT01MAU) thermostats reliably over ESP32
Bluetooth proxies, 24/7.

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
