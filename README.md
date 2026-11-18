# BrightSign for Home Assistant

[![Release](https://img.shields.io/github/v/release/Videobarista/brightsign-ha)](https://github.com/Videobarista/brightsign-ha/releases)
[![HACS](https://github.com/Videobarista/brightsign-ha/actions/workflows/hacs.yml/badge.svg?branch=main)](https://github.com/Videobarista/brightsign-ha/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/Videobarista/brightsign-ha/actions/workflows/hassfest.yml/badge.svg?branch=main)](https://github.com/Videobarista/brightsign-ha/actions/workflows/hassfest.yml)
[![Ruff](https://github.com/Videobarista/brightsign-ha/actions/workflows/ruff.yml/badge.svg?branch=main)](https://github.com/Videobarista/brightsign-ha/actions/workflows/ruff.yml)
[![Tests](https://github.com/Videobarista/brightsign-ha/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/Videobarista/brightsign-ha/actions/workflows/tests.yml)
[![CodeQL](https://github.com/Videobarista/brightsign-ha/actions/workflows/codeql.yml/badge.svg?branch=main)](https://github.com/Videobarista/brightsign-ha/actions/workflows/codeql.yml)
[![Quality scale: Bronze](https://img.shields.io/badge/quality%20scale-Bronze-cd7f32)](#quality-scale)
[![Local only](https://img.shields.io/badge/local-only-brightgreen)](#how-it-works)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Videobarista&repository=brightsign-ha&category=integration)

Custom integration for BrightSign media players. It finds players on your network and creates
entities for whatever each player offers. **Local network only** — BrightSign cloud is not used.

## How it works

A BrightSign can expose several local interfaces depending on its OS version and on how its
presentation was made. The integration detects them per player:

| Interface | When available | What you get |
|---|---|---|
| **DWS REST API** (`/api/v1`, digest auth) | BrightSignOS 8.x+ with local DWS enabled | OS version, uptime, video mode, HDMI display connected, HDMI output on/off, reboot, CEC display on/standby |
| **Presentation Local Web Server** (port 8080) | BrightAuthor presentation with Local Web Server enabled | Autorun version, one sensor per user variable, one button per UDP input event |
| **Sync multicast** (224.0.126.10:1539, passive) | Player is the leader of a sync group | Sync group running, current sync item, when it started, last item duration (loop length) |
| **Network reachability** | Always | Online sensor |

Only documented control/API interfaces are used. Web interface pages are never scraped, so on
players without the REST API (older OS versions) you get the online and sync entities, plus the
`brightsign.send_udp` action.

## Discovery

Players are discovered automatically by their BrightSign MAC address (`90:AC:3F`), DHCP hostname
(`brightsign-<serial>`) or the mDNS advertisement of a BrightAuthor presentation. You can also add
a player manually by IP address.

The **DWS password** belongs to the local Diagnostic Web Server (user is always `admin`). The
factory default is the player's serial number; leave the field empty if the player has none. If
the password changes later, Home Assistant asks for the new one (re-authentication).

## Installation

1. Click the **Open in HACS** button above, or in HACS add `https://github.com/Videobarista/brightsign-ha`
   as a custom repository (category: Integration).
2. Install **BrightSign** and restart Home Assistant.
3. Discovered players appear under **Settings → Devices & services**; or click **Add integration**
   and search for **BrightSign**.

## Actions

### `brightsign.send_udp`

Sends a UDP message, e.g. to trigger a UDP input event of a BrightAuthor presentation.

| Field | Description |
|---|---|
| `host` | Player IP, or a broadcast address such as `255.255.255.255` |
| `port` | UDP receiver port of the presentation (default `5000`) |
| `message` | Command text as defined in the presentation |

## Notes

- Sync entities only fill on the player that is the sync **leader**; followers do not send sync
  traffic. Encrypted sync traffic (BrightSignOS 8+ default) counts as activity but the item cannot
  be read.
- "Sync item started" is the time Home Assistant saw the item (re)start. "Last sync item duration"
  is calculated from the player's own timestamps.
- The DWS uses a self-signed certificate; certificate verification is disabled for the player only.

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
