# Home Assistant

Cove ships a Home Assistant integration. It shows your Cove server, its zones
and every thin client as devices, and with a **Control** token it can drive
the thin clients: switch screens on and off, set the volume, keep a screen
awake, sign it out, restart its browser, or reboot it.

Home Assistant polls Cove every 30 seconds over the HTTPS address you already
use. Cove opens no port and Home Assistant needs no inbound access, so nothing
new is exposed.

## Set it up

1. In Cove, open **Admin → Integrations** and click **Home Assistant
   integration** to download `cove-home-assistant.zip`.
2. Unzip it into Home Assistant's config folder (the one holding
   `configuration.yaml`). It adds `custom_components/cove`. Restart Home
   Assistant.
3. Back in **Admin → Integrations**, click **New token**. Pick **Control** to
   control thin clients and see their screenshots, or **Read** for status only.
   Copy the token: Cove shows it once.
4. In Home Assistant, go to **Settings → Devices & services → Add integration →
   Cove**. Enter Cove's `https://` address (for example
   `https://cove.example.com`) and the token. Plain `http://` is refused, except
   to the same machine (`localhost`), because the token would cross the
   network unencrypted.

   Untick **Verify the TLS certificate** only for a Cove on your own network
   with a self-signed certificate (the LAN TLS overlay).

The integration needs Home Assistant 2026.10 or newer. To update it, download
the zip again after updating Cove, unzip it over the old folder, and restart
Home Assistant.

## What you get

**Cove** (the server)

| Entity | Meaning |
|---|---|
| Users, Active users | Accounts, and accounts with a running workspace. |
| Users on thin clients | Accounts signed in on at least one thin client. |
| Workspaces, Running / Starting / Stopped / Failed workspaces | Workspace counts by state. |
| Thin clients online, Thin clients in use | Devices checking in, and devices with someone signed in. |

**Each zone** (including *Local*, the control plane's own Docker host)

| Entity | Meaning |
|---|---|
| Online | The zone's Docker daemon answers Cove's status monitor (every 10 s). |
| Workspaces, Running workspaces, Active users | Counts for this zone. |
| Workspace CPU, Workspace memory | Summed over the zone's running workspaces. CPU is in percent of one core, so 250 % is two and a half cores. Unknown while none of them reports. |

**Each thin client**

| Entity | Meaning |
|---|---|
| Online | Checked in within the last 75 seconds. |
| In use, Signed-in user | Who is signed in on the screen. |
| Workspace on screen | The workspace the screen is showing right now. |
| Playing audio | Something is playing on the device. |
| Volume level | The output volume (percent; up to 150). |
| Idle time | Seconds since the last keyboard, mouse or touch input. Disabled by default: it changes on every poll. |
| Last seen, Booted, Updates available, Reboot required, Agent version, Browser running | Diagnostics. IP address and Last error are there too, disabled by default. |

With a **Control** token, also:

| Entity | Does |
|---|---|
| Screenshot (image) | The last screenshot. Press **Take screenshot** to refresh it. |
| Screen (switch) | On wakes the screen; off blanks it until someone touches it. The device's own idle timer still applies. |
| Mute (switch), Volume (slider) | Set the output. Setting a volume also unmutes. |
| Keep awake (switch) | Treat the device as active (no screen-off, no idle sign-out) for up to 24 hours, until switched off. |
| Identify | A banner with the device's name and address for 10 seconds. |
| Take screenshot, Restart browser, Sign out | As in Admin → Thin clients. |
| Reboot, Power off | Power off is disabled by default: nothing in Home Assistant can turn the device back on. |

Readings from a thin client go *unknown* while it is offline, rather than
showing its last values. Controls that act on the device go *unavailable*;
**Keep awake** and **Sign out** stay usable because Cove itself holds them.
After a control, Home Assistant reads the state again 3 seconds later. Until the
device reports, the switch shows the value you just set.

Thin clients and zones you add to Cove appear in Home Assistant at the next
poll. Ones you delete are removed.

## Examples

Blank the workbench screen when nobody is in the room:

```yaml
automation:
  - alias: Workbench screen follows presence
    triggers:
      - trigger: state
        entity_id: binary_sensor.workshop_presence
    actions:
      - action: "switch.turn_{{ 'on' if trigger.to_state.state == 'on' else 'off' }}"
        target:
          entity_id: switch.workbench_screen
```

Keep the living room screen awake while the TV scene is on:

```yaml
automation:
  - alias: Keep the lounge screen awake for films
    triggers:
      - trigger: state
        entity_id: input_boolean.film_night
    actions:
      - action: "switch.turn_{{ trigger.to_state.state }}"
        target:
          entity_id: switch.lounge_keep_awake
```

Your entity ids depend on the device names and areas, so check them under the
device page.

## Tokens and security

- A token works only on `/api/integration/v1`. It is not a sign-in. It can't
  open workspaces, read files, or reach any admin endpoint, and a sign-in
  session can't be used in its place.
- **Read** sees counts, names of users signed in on thin clients, workspace
  names on screen and device addresses: who is at which screen. Give it to
  anything you would let see that, and remember Home Assistant keeps history.
- **Control** can also see and take screenshots (what someone has on their
  screen), queue `screen_on`, `screen_off`, `set_volume`, `set_mute`,
  `identify`, `screenshot`, `restart_browser`, `reboot` and `poweroff`, sign a
  device out, and set keep-awake. It can't read device logs or run system
  updates; those stay in Admin → Thin clients.
- A token acts with the authority of the admin who created it. Cove revokes it
  when that account is deleted or is no longer an admin (each revocation is in
  the audit log), so create long-lived tokens from an account that stays.
- Only the token's sha256 is stored. **Admin → Integrations** shows when and
  from where each token was last used. Revoking one stops it at once, and Home
  Assistant then asks for a new token (*Reconfigure* on the integration).
- Every command shows in the device's *Activity* list as
  `integration:<token name>`. Screenshots, reboot, power off and browser
  restarts, sign-outs, and creating or revoking tokens also go to the audit log.
  Screen and volume changes don't, so a motion automation can't flood it.
- **Download diagnostics** in Home Assistant redacts the token, Cove's address,
  device addresses and host names, and who is signed in where, so it is safe to
  attach to a bug report.

## API

Home Assistant uses these endpoints. Anything else that can send a bearer token
can use them too.

| Method | Path | Scope | Purpose |
|---|---|---|---|
| GET | `/api/integration/v1/status` | read | Users, workspaces, zones and thin clients in one JSON document, plus the token's own name and scope. |
| GET | `/api/integration/v1/thin-clients/{id}/screenshot.png` | control | The latest screenshot (`404` if none). |
| POST | `/api/integration/v1/thin-clients/{id}/commands` | control | `{"kind", "args"}`. For example `{"kind": "set_volume", "args": {"level": 60}}` or `{"kind": "set_mute", "args": {"muted": true}}`. `202` once queued. |
| POST | `/api/integration/v1/thin-clients/{id}/signout` | control | Sign the device out. |
| POST | `/api/integration/v1/thin-clients/{id}/keep-awake` | control | `{"minutes": 0..1440}` (0 clears it). |

`{id}` is the thin client's public id (the `id` in the status document). Send
`Authorization: Bearer cit_…`. An unknown or revoked token gets `401`, and a
Read token trying to control gets `403`.

```bash
curl -s -H "Authorization: Bearer $COVE_TOKEN" https://cove.example.com/api/integration/v1/status | jq .workspaces
```

## Troubleshooting

- **"Can't reach Cove"** while adding it: check the address from the Home
  Assistant host, and whether a proxy in front of Cove asks for a login. Cove
  must answer with JSON. With a self-signed certificate, untick **Verify the
  TLS certificate**.
- **Controls or screenshots missing**: the token is **Read**. Create a **Control** token and
  enter it under the integration's *Reconfigure*.
- **Volume or Mute does nothing**: the device runs an agent older than 1.1.0.
  Agents update themselves within a minute of Cove being updated, so check
  **Agent version**. Also check that the device has a sound card (Admin → Thin
  clients says *no audio output* when it doesn't).
- **Zone CPU and memory unknown**: none of the zone's running workspaces
  answered a Docker stats read. Its other sensors still work.
