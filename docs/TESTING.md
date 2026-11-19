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

## 3. Demo presentation without BrightAuthor

`demo/ha-demo/autorun.brs` is a presentation for a **spare** SD card that offers everything
the integration can pick up from a presentation:

- a Local Web Server on port 8080 with 3 user variables (Title, Message, Mode) and 6 UDP
  commands (Play, Pause, Stop, Next, Previous, Ping), and an mDNS advertisement
- UDP messages to port 5000: `bs:start`, `bs:state:<state>`, `bs:item:<n>`,
  `bs:var:<name>=<value>`, `bs:pong` and `bs:heartbeat:<n>` every 60 seconds
- the status on screen (so it also shows in the screenshot camera)

By default it switches the player's wired network to **DHCP** once (older sync scripts often
leave a fixed address behind); set `useDhcp = false` at the top to keep the current settings.

Checklist after booting the demo (within 5 minutes, or choose Reconfigure right away):

| Check | Expected |
| --- | --- |
| Autorun version | `ha-demo 1.1` |
| Variable Title / Message / Mode | Values shown; changing one changes the screen |
| UDP Play / Pause / Next / Ping buttons | Screen changes; **UDP message** shows `bs:state:...`, `bs:item:...` or `bs:pong` |
| `brightsign.send_command` (REST players) | Same as the buttons |
| Unit name | The unit name set on the player (none when it has no unit name) |

Quick check in a browser: `http://<player>:8080/GetID` shows the player's XML identity.

## 4. Sync groups

Sync entities appear on the sync **leader** as soon as its traffic arrives. Home
Assistant must be in the same network segment as the players. With debug logging:

```yaml
logger:
  logs:
    custom_components.brightsign: debug
```

the log shows which sources it receives: `Receiving BrightSign sync traffic from <address>`,
`Receiving PTP traffic from <address> (domain n, master/follower)` and
`Receiving Ethernet sync commands from <address>`.

| Check | Expected |
| --- | --- |
| Sync role | Leader on the leader, Follower on followers (PTP), Leader on the master of Ethernet sync scripts |
| Sync group / PTP domain | The same domain on all players of one group |
| Two leaders in one group (test by starting a second leader) | After 30 s *Sync conflict* on, and a repair message listing both |
