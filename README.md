# Mitsubishi MA Touch for Home Assistant

[![hacs][hacs-badge]][hacs]
[![GitHub Release][release-badge]][releases]

A Home Assistant integration for **Mitsubishi MA Touch** wired thermostat
controllers (**PAR-CT01MAU**) over **Bluetooth LE** — fully local, no Kumo Cloud,
no MELRemo app, no internet. It's built to run **several thermostats at once,
24/7**, across **ESP32 Bluetooth proxies**, with persistent connections and
self-healing recovery.

Developed and run against **five units** (4× `PAR-CT01MAU` + 1× `PAR-CT01MA`)
spread across a building on two ESP32 proxies.

It focuses on doing a few things well:

- 🌡️ **Full climate control** — HVAC mode (off / auto / heat / cool / dry /
  fan-only), heat & cool setpoints (incl. auto-mode range), fan speed
  (auto / low / medium / high / quiet), and vane swing (on/off).
- 🔗 **Persistent BLE connections** — a lightweight keepalive holds the link open
  so each poll is a single status read instead of a full reconnect + login.
- 📡 **Multi-proxy load balancing** — connections are spread across your ESP32
  Bluetooth proxies (least-loaded reachable proxy per device), and **rebalanced
  automatically** as proxies come and go.
- 🩺 **Diagnostic telemetry** — per-thermostat connection uptime, reconnects,
  latency, serving proxy and link RSSI, plus a service to export raw event data.
- ➕ **Add several at once, manage individually** — scan-and-multiselect to add a
  batch; add or remove a thermostat later without disturbing the others.
- 🇺🇸 **Correct Fahrenheit** — uses Mitsubishi's own °F lookup table so the Home
  Assistant card matches the physical controller exactly (no off-by-one).

## Why Bluetooth?

MA Touch controllers are a **wired MA-bus** accessory; their only local, cloudless
control path is **Bluetooth LE** (the same one the MELRemo app uses). The
alternative — Kumo Cloud — means an internet dependency, extra wiring/adapters,
and a vendor account. This integration talks to each controller directly over BLE:

- **Local and private** — state and commands never leave your network.
- **No app, no cloud account** — just the PIN printed on the controller.
- **Scales over your LAN** — because the BLE link is reached through **ESP32
  Bluetooth proxies**, the thermostats don't have to be near the Home Assistant
  host; the integration shares the proxy radios across all of them.

## Requirements

- **Home Assistant 2025.3 or newer.** The integration uses **config subentries**
  (one parent entry, one removable “thermostat” subentry per device), which that
  release introduced. HACS enforces the minimum; developed/run on 2026.6.
- **A Bluetooth transport that can reach your thermostats** — see
  [Before you start](#before-you-start-bluetooth-reach--your-pin). One or more
  **ESP32 Bluetooth proxies** (ESPHome `bluetooth_proxy`, *active* mode) is the
  recommended setup for more than a unit or two; a local HA Bluetooth adapter
  also works for nearby thermostats.
- **The controller PIN** for each thermostat — the 4-digit password printed on
  the controller (the same one MELRemo asks for).
- The protocol library (`construct`, `construct-typing`) is pulled in
  automatically; nothing to install by hand.

## Supported devices

| Model | Status |
|---|---|
| **PAR-CT01MAU** | ✅ Verified (4 units) |
| **PAR-CT01MA** | ✅ Verified (1 unit) — a slightly longer status frame; the fields the integration uses decode identically |
| Other MA Touch (`M/R_CT01MA*`) controllers | Expected to work (same BLE protocol) — please [open an issue][issues] if you try one |

> The library is a community reverse-engineering of the MA Touch BLE protocol, not
> an official Mitsubishi API. The wire protocol is **Celsius-native** in 0.5 °C
> steps; everything else (including °F) is handled on the Home Assistant side.

## Before you start: Bluetooth reach + your PIN

**1. Give Home Assistant Bluetooth reach to the thermostats.** MA Touch controllers
advertise quietly and their control service is **GATT-only (not advertised)**, so
they need a *connectable* Bluetooth path:

- **Recommended — ESP32 Bluetooth proxies.** Flash one or more ESP32 boards with
  ESPHome's [`bluetooth_proxy`](https://esphome.io/components/bluetooth_proxy.html)
  in **active** mode and place them near your thermostats. Each proxy serves ~3
  concurrent connections, so two proxies comfortably cover 4–5 units. The
  integration discovers them through Home Assistant's Bluetooth stack and balances
  connections across them automatically.
- **Or a local adapter** — a built-in/USB Bluetooth adapter on the HA host works
  for one or two thermostats within radio range.

**2. Find each controller's PIN.** It's the numeric password printed on (or
supplied with) the controller — the same one the MELRemo app requires. You enter
it per-thermostat when adding it.

## Installation

### HACS (recommended)

1. HACS → ⋮ → **Custom repositories** → add
   `https://github.com/mikenemat/hass-mitsubishi_matouch`, category **Integration**.
2. Install **Mitsubishi MA Touch**, then restart Home Assistant. HACS handles
   updates from there.

### Manual

Copy `custom_components/mitsubishi_matouch` into your Home Assistant
`config/custom_components/` directory and restart.

## Configuration

Once a thermostat is in range of a proxy/adapter, Home Assistant may surface a
discovered **Mitsubishi MA Touch** card automatically; otherwise go to
**Settings → Devices & Services → Add Integration → Mitsubishi MA Touch**.

1. **Searching** — the flow shows a live *“Searching for MA Touch thermostats…”*
   spinner and **advances the moment any are found** (no dead-end “none found”
   abort; if a unit needs a moment to appear it just shows up). If nothing turns
   up after a sweep, you get a one-click **Search again**.
2. **Pick** — tick the thermostats to add; you can add several in one pass.
   Already-configured ones are filtered out.
3. **Name + PIN** — give each selected thermostat a name and enter its PIN.

### Add or remove a thermostat later

The integration is a single hub entry with one **subentry per thermostat**, so you
can grow or shrink the set without redoing everything:

- **Add:** the integration's **＋ Add thermostat** action runs the same scan for
  any new units. Adding one **does not** reconnect or disturb the thermostats you
  already have.
- **Remove:** delete a thermostat's subentry; its device, entities, and BLE
  connection are cleaned up and the others keep running.
- **Rename / change PIN:** the subentry's **Reconfigure** action.

### Options

**Settings → the integration → Configure** (applied live, no reconnect):

| Option | Default | Notes |
|---|---|---|
| **Polling interval** (s) | `10` | Minimum `5`. Cheap, because the connection is persistent. |
| **Log every poll to the telemetry file** | off | Verbose; writes each poll to the JSONL log. |
| **Capture raw status frames** | off | Records the raw status frame (on change) for protocol debugging. Leave off for 24/7 use. |

## Entities

One Home Assistant **device per thermostat**, with:

### Climate

A full `climate` entity — HVAC mode, current temperature, target setpoint (and
heat/cool range in auto mode), fan speed, and swing. Temperatures are presented in
**your Home Assistant unit system** (see [Temperature & Fahrenheit](#temperature--fahrenheit)).

### Diagnostic sensors

Per thermostat, for characterizing the BLE/proxy link over time
(all under the *Diagnostic* category):

| Sensor | Unit | What it tells you |
|---|---|---|
| Connection uptime | s | How long the current BLE link has been held (keepalive working) |
| Reconnects | count | Successful reconnects since startup |
| Disconnects | count | Link drops observed |
| Poll latency | ms | Round-trip time of the last status read |
| Active proxy | — | Which ESP32 proxy is currently serving this thermostat |
| Signal strength | dBm | Link RSSI (captured at connect) |

## Temperature & Fahrenheit

MA Touch controllers work internally in **Celsius (0.5 °C steps)**; the °F shown on
the controller is a Mitsubishi **lookup table**, *not* the math conversion (e.g.
the controller shows 22.5 °C as **72 °F**, where the plain formula gives 72.5). If
an integration reports Celsius and lets Home Assistant convert, the card ends up
~1 °F off from the physical controller.

This integration avoids that:

- **If your Home Assistant unit system is °F**, the climate entity reports
  Fahrenheit **natively, using Mitsubishi's table**, so the HA card matches the
  controller exactly and steps in whole °F like the controller does.
- **If it's °C**, values pass through unchanged in 0.5 °C steps.

> [!NOTE]
> **Upgrading an existing °F install:** the entity's reported unit changes from °C
> to °F, so `current_temperature` history shows a one-time discontinuity and Home
> Assistant may raise a “units changed” repair (one click to resolve). °C setups
> are unaffected. In °F mode the settable range matches the controller's °F table
> (61–88 °F = 16.0–30.5 °C); the top 31.0 °C step is only reachable in °C mode.

## Telemetry & diagnostics

For tuning and R&D, connection behavior is captured three ways:

- **`mitsubishi_matouch.get_telemetry` service** — returns recent
  connect/disconnect/poll events (latency, proxy, RSSI, uptime) as response data.
  Optional `mac` filter; `limit` up to 1000.
- **Download Diagnostics** on the integration — a redacted snapshot (PIN removed)
  with recent telemetry and proxy assignments.
- **Diagnostic sensors** (above) for long-term charting in HA history.
- An optional append-only **JSONL log** in your config dir (off by default; enable
  *Log every poll* / *Capture raw status frames* in Options when investigating).

## Resilience — what survives without intervention

This integration is built to be left running 24/7 across flaky radios:

- **Idle disconnects** → a keepalive read holds the link open (the controller
  otherwise drops idle BLE connections after ~16 s), so polls stay cheap.
- **One or both proxies going offline** → affected thermostats are marked
  *unavailable* (only while genuinely unreachable) and **recover within a poll**
  the moment a proxy returns. A returning proxy also triggers an immediate retry,
  so recovery isn't gated on backoff.
- **A unit out of range / powered off** → no connection hammering; per-device
  **exponential backoff with jitter** stretches the retry cadence and snaps back
  to normal on the first success.
- **Proxies coming back / topology changes** → connections **rebalance** toward an
  even spread across the reachable proxies (single-flight + debounced, so a flap
  can't cause a bounce storm).
- **HA restart / integration reload** → connections are re-established and
  rebalanced; adding or removing one thermostat never disturbs the others.

## Troubleshooting

- **“No MA Touch thermostats found”** → the unit isn't currently reachable by a
  proxy/adapter. Confirm it's powered, that an ESP32 proxy is online and in range,
  and that the proxy is in **active** mode. The search auto-retries; you can also
  click **Search again**.
- **Authentication fails / thermostat stays unavailable** → wrong PIN. Use the
  thermostat's **Reconfigure** action to correct it (PIN = the password printed on
  the controller, entered as 4 digits).
- **Card shows half-degrees or is 1 °F off** → you're on an older build; update to
  the current release, which presents native °F via Mitsubishi's table.
- **An `E2` flashes on the controller** → that's a **controller-local** indicator
  and is *not* reported in the BLE status frame; the system otherwise operates
  normally. (Confirmed: status frames are byte-identical while `E2` is showing.)
- **`Active proxy` / `Signal strength` show *unknown*** → expected briefly while a
  thermostat is disconnected; they populate again on the next successful connect.

## Brand icon

Logos/icons ship in `custom_components/mitsubishi_matouch/brand/`. For them to
appear in the Home Assistant UI they must be submitted to the
[home-assistant/brands](https://github.com/home-assistant/brands) repository under
`custom_integrations/mitsubishi_matouch/`.

## Credits

This is a hardened fork of
[**cyaneous/hass-mitsubishi_matouch**](https://github.com/cyaneous/hass-mitsubishi_matouch)
by Cyaneous, Inc., which did the original MA Touch BLE reverse-engineering and the
embedded `btmatouch` protocol library. This fork adds persistent connections, ESP32
proxy load-balancing, telemetry, the config-subentry workflow, resilience
hardening, and the Fahrenheit fix. The non-linear °F↔°C table is derived from the
[MitsubishiCN105ESPHome](https://github.com/echavet/MitsubishiCN105ESPHome) project.

## License

[MIT](LICENSE) © Cyaneous, Inc. (original work). Fork changes contributed under the
same MIT license.

[hacs]: https://github.com/hacs/integration
[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
[release-badge]: https://img.shields.io/github/v/release/mikenemat/hass-mitsubishi_matouch
[releases]: https://github.com/mikenemat/hass-mitsubishi_matouch/releases
[issues]: https://github.com/mikenemat/hass-mitsubishi_matouch/issues
