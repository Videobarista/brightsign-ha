# Security Policy

## Supported versions

Only the latest release of this integration receives fixes.

| Version | Supported |
| ------- | --------- |
| 0.4.x   | Yes       |
| < 0.4   | No        |

## Reporting a vulnerability

Please do **not** open a public issue for security problems.

Use GitHub's private vulnerability reporting instead:
**Security** tab → **Report a vulnerability** on
<https://github.com/Videobarista/brightsign-ha>.

Include what you found, how to reproduce it and which version you use. You
can expect a first reply within a week.

## Scope

This integration talks to BrightSign players on your local network. Keep in mind:

- A player's Local Web Server (port 8080) and UDP receiver have no
  authentication: anyone on the same network can trigger presentation events.
  Keep players in a separate network segment.
- The DWS password is stored in the Home Assistant config entry (like every
  integration) and is redacted from diagnostics, as are host, serial number,
  MAC address, certificate fingerprint, player names and user variables.
  Protect your Home Assistant host and backups accordingly.
- With HTTPS the player's self-signed certificate is pinned at setup. Confirm a
  changed certificate only when you know why it changed (firmware update,
  factory reset).

The README has a [Security section](README.md#security) with what the
integration does and recommended network settings.
