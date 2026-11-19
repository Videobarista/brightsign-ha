# Testing with real players

A checklist for testing every feature, and how to make a player send the data the
integration can pick up.

## 1. Players with the DWS REST API (BrightSignOS 8+, e.g. XT1144)

| Check | Expected |
| --- | --- |
| Screenshot camera | Shows what the player displays (refreshes at most every 60 s, see Options) |
| Video mode select | Lists the modes of the connected display; changing it changes the output (until the presentation restarts) |
| Connected display | Manufacturer and model of the display (from its EDID) |
| SD card free / used | Free space and usage |
| HDMI output switch, CEC buttons | Display goes off/on or to standby |
| `brightsign.send_command` | The presentation receives the text as a UDP message on port 5000 |

## 2. A BrightAuthor:connected presentation that talks to Home Assistant

Menu names can differ slightly between BrightAuthor:connected versions.

1. **Networking** — Presentation settings > Interactive > Networking:
   - *UDP destination address*: the IP address of Home Assistant (or `255.255.255.255`)
   - *UDP destination port*: `5000` (or set the same port under the player's Options in HA)
   - *UDP receiver port*: `5000`
2. **Send UDP when something happens** — on a media item or state, add the command
   *Send UDP* with a text such as `video:intro`. Every message appears in Home
   Assistant as a **UDP message** event and as **Last UDP message**.
3. **UDP input events** — add *UDP Input* events (e.g. `play`, `stop`, `next`) with a
   label. With the Local Web Server on, each one becomes a **UDP <label>** button.
4. **User variables** — add a few user variables. With the Local Web Server on, each
   one becomes a **Variable <name>** text entity you can read and change.
5. **Local Web Server** — switch it on in the player setup (Setup > advanced/network
   settings, "Local Web Server"), publish the presentation, then remove and re-add the
   player in Home Assistant (or use Reconfigure) so it detects the web server.

## 3. Quick UDP test without BrightAuthor

`demo/udp-demo/autorun.brs` is a tiny presentation for a **spare** SD card. The player
sends `demo:start` and a heartbeat every 10 seconds to port 5000, and answers anything
it receives on port 5001 with `demo:ack:<message>`.

- In Home Assistant, the **UDP message** event and **Last UDP message** sensor appear
  after the first message.
- Call `brightsign.send_udp` with the player's IP, port `5001` and a message: the screen
  shows it and Home Assistant receives `demo:ack:<message>`.

## 4. Sync groups

Sync entities appear on the sync **leader** as soon as its traffic arrives. Home
Assistant must be in the same network segment as the players. With debug logging:

```yaml
logger:
  logs:
    custom_components.brightsign: debug
```

the log shows `Listening for BrightSign sync traffic` and
`Receiving BrightSign sync traffic from <address>`.
