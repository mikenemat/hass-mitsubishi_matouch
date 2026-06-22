# Changelog

This is a hardened fork of [cyaneous/hass-mitsubishi_matouch](https://github.com/cyaneous/hass-mitsubishi_matouch),
focused on running several MA Touch (PAR-CT01MAU) thermostats reliably over ESP32
Bluetooth proxies, 24/7.

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
