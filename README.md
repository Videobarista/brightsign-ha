# BrightSign for Home Assistant

[![Release](https://img.shields.io/github/v/release/Videobarista/brightsign-ha?sort=semver)](https://github.com/Videobarista/brightsign-ha/releases)
[![Ruff](https://github.com/Videobarista/brightsign-ha/actions/workflows/ruff.yml/badge.svg?branch=main)](https://github.com/Videobarista/brightsign-ha/actions/workflows/ruff.yml)
[![Hassfest](https://github.com/Videobarista/brightsign-ha/actions/workflows/hassfest.yml/badge.svg?branch=main)](https://github.com/Videobarista/brightsign-ha/actions/workflows/hassfest.yml)
[![HACS](https://github.com/Videobarista/brightsign-ha/actions/workflows/hacs.yml/badge.svg?branch=main)](https://github.com/Videobarista/brightsign-ha/actions/workflows/hacs.yml)
[![Tests](https://github.com/Videobarista/brightsign-ha/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/Videobarista/brightsign-ha/actions/workflows/tests.yml)
[![CodeQL](https://github.com/Videobarista/brightsign-ha/actions/workflows/codeql.yml/badge.svg?branch=main)](https://github.com/Videobarista/brightsign-ha/actions/workflows/codeql.yml)
[![Local only](https://github.com/Videobarista/brightsign-ha/actions/workflows/local-only.yml/badge.svg?branch=main)](https://github.com/Videobarista/brightsign-ha/actions/workflows/local-only.yml)
[![IoT class](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2FVideobarista%2Fbrightsign-ha%2Fmain%2Fcustom_components%2Fbrightsign%2Fmanifest.json&query=%24.iot_class&label=IoT%20class&color=41BDF5)](#local-only)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Quality scale: Bronze](https://img.shields.io/badge/quality%20scale-aligned%20with%20Bronze-cd7f32.svg)](#quality-scale)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/Videobarista/brightsign-ha/badge)](https://scorecard.dev/viewer/?uri=github.com/Videobarista/brightsign-ha)

[![Open your Home Assistant instance and open this repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Videobarista&repository=brightsign-ha&category=integration)

Custom integration for BrightSign media players. It finds players on your network and creates
entities for whatever each player offers. **Local network only** — BrightSign cloud is not used.

> [!IMPORTANT]
> This is an unofficial, community made integration. It is **not affiliated with, endorsed by
> or supported by BrightSign LLC.**

## How it works

A BrightSign can expose several local interfaces depending on its OS version and on how its
presentation was made. The integration detects them per player:

| Interface | When available | What you get |
|---|---|---|
| **DWS REST API** (`/api/v1`, digest auth) | BrightSignOS 8.x+ with local DWS enabled | OS version, uptime, video mode (read and **change**), HDMI display connected, **connected display** (make/model from EDID), HDMI output on/off, **screenshot camera**, **SD card free/used**, reboot, CEC display on/standby, `brightsign.send_command` |
| **Presentation Local Web Server** (port 8080/8008) | BrightAuthor presentation with Local Web Server enabled | Autorun version, one **text entity per user variable** (read and change), one button per UDP input event |
| **UDP messages from the presentation** (receive-only) | Presentation sends UDP (e.g. *Send UDP* command) to Home Assistant | **UDP message** event entity, **Last UDP message** sensor, `brightsign_udp_message` bus event |
| **Sync multicast** (224.0.126.10:1539, passive) | Player is the leader of a sync group | Sync group running, current sync item, when it started, last item duration (loop length) |
| **Info page of the Diagnostic Web Server** (read-only fallback) | Older BrightSignOS without the REST API, DWS enabled | Player name, model, serial number, OS version, video mode (resolution), uptime, PoE |
| **Network reachability** | Always | Online, IP address and serial number sensors |

The REST API, the presentation's XML API, UDP and sync multicast are documented interfaces.
For players without the REST API (older BrightSignOS) the integration reads the **Info page** of
the Diagnostic Web Server as a fallback: read-only, one page, nothing is changed on the player.

Players are named after the name they report (REST API, presentation or Info page), not their
IP address. Sync entities are only created for players that are a sync **leader**.

## Discovery

Players are discovered automatically by their BrightSign MAC address (`90:AC:3F`), DHCP hostname
(`brightsign-<serial>`) or the mDNS advertisement of a BrightAuthor presentation. You can also add
a player manually by IP address.

The **DWS password** (local Diagnostic Web Server, user always `admin`) is **optional**. The
factory default is the player's serial number. Without it — or when the player has no DWS — the
player is still added with the basic entities (online, sync, UDP). Add or change the password
later with **Settings → Devices & services → BrightSign → ⋮ → Reconfigure**. If a stored password
stops working, Home Assistant asks for the new one (re-authentication).

## Installation

1. Click the **Open in HACS** button above, or in HACS add `https://github.com/Videobarista/brightsign-ha`
   as a custom repository (category: Integration).
2. Install **BrightSign** and restart Home Assistant.
3. Discovered players appear under **Settings → Devices & services**; or click **Add integration**
   and search for **BrightSign**.

## Options

**Settings → Devices & services → BrightSign → ⋮ → Options** per player:

| Option | Default | Meaning |
|---|---|---|
| UDP port for messages from the presentation | 5000 (or the port the presentation reports) | Where the presentation sends its UDP messages (*UDP destination port*) |
| Seconds between screenshots | 60 | Minimum time between two screenshots; each one is written to, and removed from, the player's SD card |

## Actions

### `brightsign.send_udp`

Sends a UDP message, e.g. to trigger a UDP input event of a BrightAuthor presentation.

| Field | Description |
|---|---|
| `host` | Player IP, or a broadcast address such as `255.255.255.255` |
| `port` | UDP receiver port of the presentation (default `5000`) |
| `message` | Command text as defined in the presentation |

### `brightsign.send_command`

Sends a command through the DWS REST API (players with the REST API). The player delivers
it to the presentation as a UDP message on port 5000.

| Field | Description |
|---|---|
| `device_id` | The BrightSign player |
| `command` | Command text (1-1024 printable ASCII characters) |

### Event `brightsign_udp_message`

Fired for every UDP message from a player, with `device_id`, `host` and `message`. Use it
(or the **UDP message** event entity) as an automation trigger.

## Testing and demo

[docs/TESTING.md](docs/TESTING.md) has a checklist per feature, how to set up a
BrightAuthor:connected presentation that sends UDP messages and user variables, and a tiny
UDP demo presentation (`demo/udp-demo/autorun.brs`) for a spare SD card.

## Notes

- A switched-off player is normal: its entities stay available and show it as offline (online
  off, HDMI off, other values unknown) instead of turning unavailable. Commands to an offline
  player are refused with a clear message.
- Sync monitoring needs Home Assistant in the **same network segment** (VLAN/subnet) as the
  players: sync multicast is not routed. With debug logging on, the log shows "Listening for
  BrightSign sync traffic" and "Receiving BrightSign sync traffic from <address>".
- Sync entities only fill on the player that is the sync **leader**; followers do not send sync
  traffic. Encrypted sync traffic (BrightSignOS 8+ default) counts as activity but the item cannot
  be read.
- "Sync item started" is the time Home Assistant saw the item (re)start. "Last sync item duration"
  is calculated from the player's own timestamps.

## Security

What the integration does to protect your players and Home Assistant:

- **Certificate pinning**: players with HTTPS use a self-signed certificate. Its SHA-256
  fingerprint is remembered at setup; from then on only that certificate is accepted, so nobody
  else on the network can pose as the player. A changed certificate (firmware update, factory
  reset) asks you to re-confirm the player under **Settings → Devices & services**.
- **Credentials**: HTTP digest authentication only (SHA-256 preferred, MD5 when the player only
  offers that); the password itself is never sent, never logged and is redacted from
  diagnostics together with the host, serial number, MAC address, fingerprint, player names
  and user variables.
- **Untrusted answers**: responses are limited to 1 MiB, XML with DOCTYPE/ENTITY declarations
  is refused, text is stripped of control characters and limited in length, and at most 64 user
  variables and 64 UDP events are taken from one presentation. UDP actions reported by the
  player are only used when they are plain printable text. The parsers are fuzzed in every test
  run and with [Atheris](https://github.com/google/atheris) (`tests/fuzz/`).
- **UDP commands**: the `brightsign.send_udp` action only accepts IP addresses on the local
  network (private, link-local, broadcast or multicast) and printable ASCII messages up to 1024
  characters.
- **UDP messages from presentations**: receive-only. Only datagrams from the addresses of
  configured players are accepted, at most 1 KiB, at most 20 per second per player; text
  is sanitized. Snapshot files are only downloaded and removed under `remote_snapshots`.
- **Sync listener**: receive-only. Datagrams above 2 KiB or from non-local addresses are
  ignored, and states are only written when something changes (the heartbeat arrives every
  second).
- **Gentle on the player**: one poll every 30 seconds, one command at a time.

### Local only

The integration only talks to your players, never to the internet or any other service. Two
automated checks back the **Local only** badge:

- `scripts/check_local_only.py` (the badge's workflow) reads the manifest and the code: local
  IoT class, no cloud or webhook, no extra Python packages, no hard-coded internet addresses,
  connections and UDP sends only from `api.py`. Exceptions are allowed explicitly and
  checked: the receive-only listeners in `sync.py` (sync multicast) and `udp_events.py`
  (UDP messages from configured players), which may not send anything, and the sync group
  address `224.0.126.10`.
- The tests record every HTTP request, TCP connection and UDP datagram during probing,
  polling and commands and check that all of them go to the configured player; they also check
  that the action refuses public addresses and that the sync listener ignores outside traffic.

The **IoT class** badge shows the class declared in the manifest (`local_polling`).

This covers the integration itself; what Home Assistant does (for example its optional
analytics) is up to your Home Assistant settings.

### Network advice

| Where | Setting |
| --- | --- |
| Player | Local DWS on with a strong password (BrightSignOS 9 turns it off by default), or off if you do not need REST features. Local Web Server only when the presentation needs it. |
| Network | Players in an AV/IoT VLAN with fixed addresses (DHCP reservation). No port forwarding. |
| Firewall: allow | Home Assistant → players TCP 443/80 (DWS) and 8080/8008 (Local Web Server), UDP to the presentation's receiver port (default 5000). Players → Home Assistant UDP to the configured message port (default 5000) and UDP 1539 multicast `224.0.126.10` (sync monitoring). |
| Firewall: block | Players → the rest of the LAN and the internet, except DNS/NTP (and BrightSign cloud if you use it). |

See [SECURITY.md](SECURITY.md) to report a vulnerability and [CONTRIBUTING.md](CONTRIBUTING.md)
to contribute.

## Removal

1. **Settings → Devices & services → BrightSign**, open each player and choose **Delete**.
2. Remove the integration in HACS and restart Home Assistant.

## Quality scale

This integration is aligned with the Home Assistant quality scale: **Bronze** (see
[`quality_scale.yaml`](custom_components/brightsign/quality_scale.yaml)). No official tier is
claimed; Home Assistant only grades core integrations.

## License

MIT — see [LICENSE](LICENSE). BrightSign is a trademark of BrightSign LLC; this project is not
affiliated with BrightSign. The icons are generic and can be replaced.
