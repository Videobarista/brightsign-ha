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

Home Assistant integration for BrightSign media players. It finds the players on your
network, works out what each player offers and creates entities for it: status, screenshots,
video output, presentation variables and commands, UDP messages and sync groups.
**Local network only** — BrightSign cloud is not used.

> [!IMPORTANT]
> This is an unofficial, community made integration. It is **not affiliated with, endorsed by
> or supported by BrightSign LLC.**

## Features

What a player offers depends on its BrightSignOS version, its settings and its presentation.
The integration detects this per player:

| Source | Available when | Entities and actions |
|---|---|---|
| **DWS REST API** (local Diagnostic Web Server) | BrightSignOS 8 or newer, local DWS on | OS version, uptime, video mode (shown and changed), HDMI display connected, connected display (make and model), HDMI output on/off, screenshot camera, SD card free/used, unit name, restart, CEC display on/standby, `brightsign.send_command` |
| **Info page** of the Diagnostic Web Server | Older BrightSignOS without the REST API | Name, model, serial number, OS version, video mode, uptime, PoE (read-only) |
| **Presentation web server** (BrightAuthor Local Web Server) | Presentation with the Local Web Server on | Autorun version, a text entity per user variable (read and change), a button per UDP command of the presentation |
| **UDP messages** from the presentation | Presentation sends UDP to Home Assistant | *UDP message* event entity, *Last UDP message* sensor, `brightsign_udp_message` event |
| **Sync traffic** (passive) | Player is part of a sync group | Sync role (leader/follower), sync group, PTP domain, sync conflict; on the leader also sync running, sync item, item started, last item duration |
| **Network** | Always | Online, IP address, serial number, host name |

Every player also gets a device with its model, OS version and serial number.

### Names

Players are named after the name they report (unit name, presentation or Info page), or else
their own host name on the network. A name given by the integration follows the player when
it changes (for example with a new presentation); a name you choose yourself is kept.

### Changes on the player

- **New presentation**: every 5 minutes the integration looks again for a presentation web
  server (and for the Info page on older players) and adds the new entities by itself.
- **New address**: a player that gets a new address from DHCP is followed automatically.
- **Other player at the address**: when a different player answers at a player's address (for
  example after swapping players), the player is shown offline and a repair message explains
  what happened. It clears by itself when the player is found again.
- **Switched off**: entities stay available and show the player as offline (online off, HDMI
  off, other values unknown). Commands to an offline player are refused with a clear message.

## Requirements

- Home Assistant 2025.10 or newer.
- Players in the same network as Home Assistant. Sync monitoring and UDP messages need the same
  network segment (VLAN/subnet): multicast and broadcast are not routed.
- For the REST features: the local DWS switched on (BrightSignOS 9 turns it off by default).

## Installation

1. Click the **Open in HACS** button above, or in HACS add
   `https://github.com/Videobarista/brightsign-ha` as a custom repository (category:
   Integration).
2. Install **BrightSign** and restart Home Assistant.

## Adding players

Players are found in two ways:

- **Automatically**: by their BrightSign MAC address (`90:AC:3F`), their DHCP host name
  (`brightsign-<serial>`) or the mDNS advertisement of a presentation. They appear under
  **Settings → Devices & services → Discovered**. Home Assistant sees a player through DHCP when
  it requests an address, usually at start-up.
- **Network scan**: **Add integration → BrightSign** lists the players found in Home
  Assistant's own network, by IP and MAC address. You can also type an IP address or host name.

**DWS password**: the user is always `admin`. The integration first tries the player without a
password and then with its serial number, the factory default (taken from the DHCP host name,
the presentation, the Info page or the HTTPS certificate). You are only asked for the password
when that does not work. Leave it empty to add the player with the basic entities. You can add
or change the password later with **⋮ → Reconfigure** on the player (an empty password keeps
the current one). If a stored password stops working, Home Assistant asks for the new one.

## Sync groups

The integration listens to the sync traffic on the network and shows per player:

| Entity | Meaning |
|---|---|
| **Sync role** | *Leader* or *Follower* (*None* while no sync traffic is seen) |
| **Sync group** | The group the leader runs: the roSyncManager domain of a BrightAuthor sync presentation, or `UDP 11167` for Ethernet sync scripts |
| **PTP domain** | The clock domain, shared by the leader and the followers of a group |
| **Sync conflict** | On when a group has more than one leader; the attribute lists the other leaders |

When a group has more than one leader for 30 seconds, a **repair message** lists all leaders,
also players that are not added to Home Assistant. It clears by itself.

How roles are recognized:

- **BrightAuthor sync presentations**: the leader sends roSyncManager messages
  (`224.0.126.10`, UDP 1539). Followers are recognized by their PTP clock requests
  (`224.0.1.129`, UDP 319/320).
- **Ethernet sync scripts** (for example based on Zach Poff's scripts): the master broadcasts
  short commands on UDP 11167 when its video starts. Clients do not send anything, so only the
  master is shown.
- BrightSignOS 8 and newer encrypt sync messages by default: the leader and its group are
  still shown, the sync item is not.

## Options

**⋮ → Options** on a player:

| Option | Default | Meaning |
|---|---|---|
| UDP port for messages from the presentation | 5000, or the port the presentation reports | Where the presentation sends its UDP messages |
| Seconds between screenshots | 60 | Minimum time between two screenshots |

## Actions and events

### `brightsign.send_udp`

Sends a UDP message, for example to trigger a UDP command of a presentation.

| Field | Description |
|---|---|
| `host` | Player IP address, or a broadcast address such as `255.255.255.255` |
| `port` | UDP receiver port of the presentation (default `5000`) |
| `message` | Command text as defined in the presentation |

### `brightsign.send_command`

Sends a command through the DWS REST API; the player passes it to its presentation as a UDP
message on port 5000.

| Field | Description |
|---|---|
| `device_id` | The BrightSign player |
| `command` | Command text (1–1024 printable ASCII characters) |

### Event `brightsign_udp_message`

Fired for every UDP message from a player, with `device_id`, `host` and `message`. Use it, or
the *UDP message* event entity, as an automation trigger.

## Demo presentation

[`demo/ha-demo/autorun.brs`](demo/ha-demo/autorun.brs) is a presentation for a spare SD card
that switches on everything a presentation can offer: a web server with user variables and UDP
commands, UDP messages to Home Assistant and an mDNS advertisement. It also puts the player on
DHCP. [docs/TESTING.md](docs/TESTING.md) has a checklist per feature and the steps to set up a
BrightAuthor:connected presentation that works with Home Assistant.

## Known limitations

- Players with a fixed address in another network than Home Assistant cannot be reached and
  are not found by discovery or the network scan.
- **CEC**: the CEC buttons use a DWS command that needs a display with CEC and a BrightSignOS
  that offers it; otherwise the press reports that it failed. The HDMI output switch always
  works.
- **Video mode**: a presentation can set its own video mode again when it restarts.
- **Screenshots**: each screenshot is briefly written to the player's SD card and removed
  afterwards when the player allows it; otherwise the player keeps its own number of
  snapshots.
- **Followers** are only shown when Home Assistant may listen on UDP ports 319/320 (not the
  case with another PTP service on the host, or without root rights).

## Troubleshooting

- **A player is not found**: look for MAC addresses starting with `90:AC:3F` in
  **Settings → System → Network → DHCP Browser**, or power-cycle the player, then add it by
  IP address.
- **Debug logging**:

  ```yaml
  logger:
    logs:
      custom_components.brightsign: debug
  ```

  The log then shows what each player offers ("Probe of …") and which sync sources are heard
  ("Receiving BrightSign sync traffic", "Receiving PTP traffic", "Receiving Ethernet sync
  commands").
- **Diagnostics**: **⋮ → Download diagnostics** on a player. Passwords, addresses, serial
  numbers, names and user variables are redacted.

## Security

- **Certificate pinning**: players with HTTPS use a self-signed certificate. Its SHA-256
  fingerprint is stored at setup and only that certificate is accepted afterwards. A changed
  certificate (firmware update, factory reset) asks you to confirm the player again.
- **Credentials**: HTTP digest authentication only (SHA-256 preferred, MD5 when the player only
  offers that). The password is never sent as such, never logged and redacted from diagnostics.
- **Untrusted answers**: responses are limited to 1 MiB (screenshots 16 MiB), XML with DOCTYPE or
  ENTITY declarations is refused, text is stripped of control characters and limited in length,
  and at most 64 user variables and 64 UDP commands are taken from a presentation. The parsers
  are fuzzed in every test run and with [Atheris](https://github.com/google/atheris)
  (`tests/fuzz/`).
- **UDP commands**: `brightsign.send_udp` only accepts local network addresses (private,
  link-local, broadcast or multicast) and printable ASCII up to 1024 characters.
- **Listeners** (UDP messages, sync, PTP, Ethernet sync): receive-only, nothing is ever sent
  back. Only local source addresses and small datagrams are accepted, UDP messages only from
  configured players (at most 20 per second each), PTP only counts for configured players, and
  states are only written when something changes.
- **Network scan**: only when you open **Add integration**, only in Home Assistant's own private
  IPv4 networks (at most 1024 addresses). Each address gets one TCP connection attempt on port
  80 that is closed right away; players are recognized by their MAC address in the ARP table.
- **Gentle on the player**: one poll every 30 seconds, one command at a time.

### Local only

The integration only talks to your players, never to the internet or another service. Two
automated checks back the **Local only** badge:

- `scripts/check_local_only.py` checks the manifest and the code: local IoT class, no cloud or
  webhook, no extra Python packages, no hard-coded internet addresses, connections and UDP
  sends only from `api.py`. The receive-only listeners in `sync.py`, `ptp.py` and
  `udp_events.py` and the multicast addresses `224.0.126.10` and `224.0.1.129` are the only,
  explicitly checked, exceptions.
- The tests record every HTTP request, TCP connection and UDP datagram during probing, polling
  and commands and check that all of them go to the configured player.

This covers the integration itself; what Home Assistant does (for example its optional
analytics) is up to your Home Assistant settings.

### Network advice

| Where | Setting |
| --- | --- |
| Player | Local DWS on with a strong password, or off if you do not need the REST features. Presentation web server only when the presentation needs it. |
| Network | Players in an AV/IoT VLAN with fixed addresses through DHCP reservations. No port forwarding. |
| Firewall: allow | Home Assistant → players: TCP 80/443 (DWS), TCP 8080/8008 (presentation web server), UDP to the presentation's receiver port (default 5000). Players → Home Assistant: UDP to the message port (default 5000); for sync monitoring UDP 1539 (`224.0.126.10`), UDP 319/320 (`224.0.1.129`) and UDP 11167 broadcast. |
| Firewall: block | Players → the rest of the LAN and the internet, except DNS and NTP (and BrightSign cloud if you use it). |

See [SECURITY.md](SECURITY.md) to report a vulnerability and [CONTRIBUTING.md](CONTRIBUTING.md)
to contribute.

## Removal

1. **Settings → Devices & services → BrightSign**: open each player and choose **Delete**.
2. Remove the integration in HACS and restart Home Assistant.

## Quality scale

This integration is aligned with the Home Assistant quality scale: **Bronze** (see
[`quality_scale.yaml`](custom_components/brightsign/quality_scale.yaml)). No official tier is
claimed; Home Assistant only grades core integrations.

## License

MIT — see [LICENSE](LICENSE). BrightSign is a trademark of BrightSign LLC; this project is not
affiliated with BrightSign. The icons are generic and can be replaced.
