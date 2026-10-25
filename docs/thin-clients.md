# Cove — Thin clients

A **thin client** is a small machine whose whole job is to show Cove: it boots
straight into a locked-down, full-screen browser on the Cove dashboard, and you
manage it from **Admin → Thin clients**. Sit down, sign in, and carry on in the
workspace you started somewhere else. Walk away, and it darkens its screen and
signs you out of *that screen* on its own.

Each thin client runs Debian, sway (a Wayland compositor whose only key bindings
are the volume keys, so there's nothing to escape to) and Chromium in kiosk mode, plus the small Cove
thin client agent. The agent **dials out** to Cove over HTTPS, the same way a
browser does, so a thin client needs no open port and no network setup beyond
reaching Cove.

```
  thin client (Debian)                                   Cove
 ┌──────────────────────────────────┐                ┌─────────────────────┐
 │ tty1 autologin → sway (no keys)  │                │ SPA + API           │
 │   └ Chromium --kiosk  ───────────┼─── HTTPS ─────▶│ (normal sign-in,    │
 │       wiped profile per session  │                │  device-scoped      │
 │       URL allowlist: Cove only   │                │  sessions)          │
 │ cove-thin-client agent (root) ───┼─── HTTPS ─────▶│ /api/thin-client/*  │
 │   idle · display · updates       │  long-poll,    │ policy · commands   │
 │   commands · self-update         │  outbound only │ agent file + sha256 │
 └──────────────────────────────────┘                └─────────────────────┘
```

---

## 1. What you need

- **Hardware**: anything that runs Debian and a browser well. An ex-office
  thin client (Dell Wyse 5070, HP t630/t640, Fujitsu Futro) or a mini PC is
  ideal; x86-64 is what's tested. Hardware video decode (most Intel/AMD iGPUs)
  keeps streams smooth.
- **Debian 12 (bookworm) or 13 (trixie)**, freshly installed. The netinst image
  with only *standard system utilities* selected is enough: no desktop. Or let
  the [install USB](#3-the-install-usb) do the Debian install for you.
- The machine must reach Cove's URL over HTTPS. A self-signed or private-CA
  certificate works too (see [Private certificates](#9-private-certificates)).

## 2. Set one up (Debian already installed)

1. **Admin → Thin clients → Add Thin Client.** Give it a name ("Workbench",
   "Rack") and, optionally, a location.
2. Cove shows an **enrolment code** (`XXXX-XXXX-XXXX-XXXX`) and an install
   one-liner. The code works **once**, and expires after 1 hour by default (24
   hours or 7 days if you choose, for example for an install USB).
3. On the machine, as root:

   ```bash
   curl -fsSL "https://cove.example.com/thin-client/install.sh?code=XXXX-XXXX-XXXX-XXXX" | sudo bash
   ```

It takes a few minutes. When it finishes, the console switches to the kiosk and
the card in Cove goes **online**.

What the installer does:

| Step | Detail |
|---|---|
| Enrol | Swaps the code for a long-lived device token, stored root-only in `/etc/cove-thin-client/device.token`. Only its sha256 is kept in Cove. |
| Agent | Downloads the agent from Cove and checks its sha256, into `/usr/local/lib/cove-thin-client/agent.py`. |
| Packages | sway, swaybg, chromium, grim, PipeWire, ddcutil, unattended-upgrades, plymouth, VA-API drivers, fonts. |
| Kiosk user | `kiosk`: a locked password, auto-logged-in on tty1 only, which runs sway and nothing else. |
| Lockdown | sway without XWayland and with no key bindings except volume up, down and mute, with `Ctrl+Alt+F<n>` console switching disabled. No spare consoles (`NAutoVTs=0`). A managed Chromium policy blocks every URL except Cove's own. |
| Boot | A Cove boot splash, a 1-second GRUB menu, and a capped journal. |
| Service | `cove-thin-client.service` (the agent). Chromium runs in its own transient unit, `cove-browser`. |

Re-running the installer with a new code is safe: that's also how you
**re-enrol** a device (Admin → … → *Re-enroll*). Using a new code replaces the
device's token, so the old install stops working.

## 3. The install USB

For a machine without Debian (or to rebuild one from scratch), build an install
image once and reuse it:

```bash
thinclient/iso/build-iso.sh --cove-url https://cove.example.com \
  --keymap gb --timezone Europe/London --ssh-key ~/.ssh/id_ed25519.pub
sudo dd if=cove-thin-client.iso of=/dev/sdX bs=4M conv=fsync status=progress
```

It downloads the current official Debian netinst image (and verifies its
sha256), then adds one boot entry at the top of the menu: **Install Cove thin
client (ERASES THE DISK)**. That entry installs a minimal Debian unattended on
the first disk that isn't the USB stick. Then, on first boot, the console asks
for Cove's address (prefilled) and an enrolment code, and runs the installer
above. Debian's own menu entries are left as they are, and still ask before
they touch a disk.

| Option | |
|---|---|
| `--cove-url` | Required. Prefilled on first boot. |
| `--code` | Bake in an enrolment code (mint it with a 7-day lifetime). First boot then needs no typing. If the code has expired or been used, first boot asks for a new one. |
| `--ssh-key` | Installs SSH with key-only logins for the `coveadmin` account. |
| `--password` | Password for `coveadmin` (sudo). Generated and printed if omitted. |
| `--keymap`, `--timezone`, `--locale`, `--hostname` | Install settings. The keymap also becomes the kiosk's keyboard layout. |
| `--ca-file` | Cove's certificate, if it isn't publicly trusted (see [Private certificates](#9-private-certificates)). |
| `--iso` | Use a netinst image you already have. |

The script needs `xorriso`, `curl` and `openssl`. Without them it runs itself
in a `debian:trixie` container (Docker).

> **Treat a baked-in code like a password** until it has been used: anyone who
> boots the stick before you can enrol their machine in its place. Leave
> `--code` out for a stick you keep around.

## 4. Managing thin clients

Each card in **Admin → Thin clients** shows the last screenshot, whether the
device is online, who is signed in, whether the screen is on (and how long it
has been idle), the monitor and mode, the OS with pending updates or a needed
reboot, and the agent version. A device that misses a few check-ins shows
**offline**. Commands queued for it then run when it comes back.

| Action | What it does |
|---|---|
| **Wake / Sleep** | Turn the screen on, or off until someone touches it. |
| **Sign out** | Ends the session on that screen only, and resets its browser. |
| **Screenshot** | Captures the screen now (scaled down above 2000 px wide). |
| **Identify** | Shows a banner with the device name and address for 15 s. |
| **Restart browser** | Fresh browser on a wiped profile (signs out). |
| **Keep awake for 2 hours** | Treat the device as active, for example during a film. |
| **Update system now** | `apt-get update && full-upgrade && autoremove`; the output is in *Activity*. |
| **Agent logs / System warnings** | The agent's and browser's journal, or this boot's warnings. |
| **Reboot / Power off** | After a confirmation. |
| **Activity** | The last 50 commands, with their output. |

### Settings

Changes apply within seconds on an online device, and when it next checks in
otherwise.

**Display**

- **Screen off after** N idle minutes (or never). Idle means no keyboard, mouse
  or touch input, read straight from the kernel.
- **On hours**: outside this window (device local time, may wrap midnight)
  the screen goes dark after a single idle minute.
- **Playing audio keeps it awake**: films and music count as activity.
- **Rotation**, **scale**, **brightness** (a laptop backlight, or the monitor
  itself over DDC/CI) and **volume** (up to 150%; above 100% amplifies, which
  small monitor speakers often need). Rotation and scale persist across
  reboots. Brightness and volume are set when you change them.

The keyboard's **volume keys** (up, down, mute) work on the device, up to 150%.
A device's speakers start at 100%; Debian's default of 40% is far too quiet.
The card shows the current volume, and whether it's muted.

**Session**

- **Sign out after** N idle minutes. The device resets its browser on a wiped
  profile, which signs whoever was using it out **of that screen only**. Their
  other browsers, like the upstairs desktop where they started the workspace,
  stay signed in. The workspace itself keeps running.
- **Longest session**: a hard cap on how long a sign-in on this screen lasts,
  however busy it is. Cove enforces it, not the device.
- **After sign-in**: show the dashboard; open the workspace if exactly one is
  running (the "start upstairs, carry on downstairs" case); or open a **pinned
  workspace**. A pinned workspace opens only for its owner, and is started first
  if it's halted.

**Browser**

- **Extra allowed hosts**: by default Chromium can only open Cove, your OIDC
  provider, and the workspace domain. Add hosts this screen may open directly,
  one per line (`kvm.lan`, `*.docs.example.org`). Prefer reaching such things
  *through* a Cove workspace (see [Recipes](#8-recipes)): that keeps Cove's
  sign-in, timeouts and audit log in front of them.
- **Colour scheme for web pages** (default **dark**): what pages such as your
  OIDC provider's sign-in page see as the user's preferred scheme. A kiosk has
  no desktop setting to follow, so without this Chromium tells pages "light".
  It takes effect when the browser next starts: at once if nobody is signed
  in, otherwise at the next sign-out.

**Updates**

- **Install updates every night** in the **maintenance hour**, skipped while
  someone is signed in. Debian's unattended security upgrades run regardless.
- **Reboot when an update needs it**: only in the maintenance hour, and never
  while someone is signed in.

The **agent** updates itself: whenever the agent file Cove serves changes (a
Cove upgrade), each device downloads it, checks the sha256 and restarts the
agent in place. The browser keeps running, so nobody is signed out.

## 5. Sessions and sign-out

Signing in on a thin client is ordinary Cove sign-in (local or OIDC/SSO). What
differs is that the session belongs to the **device**:

- When Chromium starts, the agent asks Cove for a one-time ticket and opens
  `/thin-client/bind?ticket=…`. That sets an httpOnly cookie naming the device.
  The device token itself never enters the browser. A browser that fails to
  bind is restarted. The page fails closed and never falls back to an unscoped
  login.
- Every token minted in that browser carries the device's id and the time the
  sign-in started. Cove accepts them only while the device is enrolled, until
  the device is signed out, and never past the **longest session** cap.
- Signing out there, by the idle timeout, the SPA's *Log out*, an admin's
  **Sign out**, or deleting the device, revokes that device's sessions and
  nothing else. A normal *Log out* elsewhere, or **Users → Revoke sessions**,
  still signs the user out everywhere, thin clients included.
- Every sign-out also wipes the browser profile, so nothing (cookies, stream
  tokens, cached pages) is left for the next person.

Idle sign-outs appear in the audit log as `thin_client.idle_signout`. Logging
out on a kiosk appears as `logout.thin_client`.

## 6. Updates in detail

| What | How |
|---|---|
| Agent | Automatic, from Cove (sha256-checked, parsed before it's swapped in, rolled out on the next check-in). |
| Missing packages | A newer agent that needs a package installs it on start. |
| Debian security updates | `unattended-upgrades`, daily. |
| Everything else | Nightly in the maintenance hour (if enabled), or **Update system now**. |
| Reboots | In the maintenance hour if allowed, or **Reboot**. |

## 7. Security model

- **No inbound exposure.** The agent only makes outbound HTTPS requests to Cove:
  a long-poll check-in about every 25 s, plus command results and screenshots.
- **The device token** is a random bearer token, stored root-only, and only as a
  sha256 in Cove. Deleting a device in Cove, or re-enrolling it, kills it at once.
- **Commands are a fixed list** (the table above). Their arguments are validated
  on both ends, and there is no "run this shell command".
- **Trust runs one way: Cove is trusted with root on its thin clients.** The agent
  self-updates from Cove, so whoever controls your Cove server can change what
  runs on them. That is the point of "updatable from Cove", and the same trust
  you already give Cove over its zones. To pin a device's agent, set
  `"allow_agent_update": false` in `/etc/cove-thin-client/config.json`. Cove can
  then still manage it, but not replace its code.
- **The kiosk is for honest users.** No key bindings beyond volume, no consoles, URL
  allowlisting and no DevTools stop casual escapes. Someone with physical access
  and time can still boot other media or enter GRUB's recovery mode. Set a BIOS
  password and boot order if that matters where the device sits.
- **Screenshots** are admin-only, taken only on request, and kept as the single
  latest one per device.

## 8. Recipes

**Workbench (videos, specs, printing, music)**: *After sign-in* = open the
running workspace; *Sign out after* 60–120 min; *Playing audio keeps it awake*
on; *Screen off after* 15 min. Start a workspace upstairs, walk down, sign in:
it opens on its own. Use **Keep awake for 2 hours** for a silent film.

**Server rack terminal**: *Screen off after* 2 min; *Sign out after* 10 min;
*Longest session* 1 hour; *On hours* empty.

**KVM console on the rack**: launch a **browser workspace** pointed at your KVM's
web UI (kiosk mode, with LAN access allowed for that workspace in its network
settings). Then set the rack thin client's *After sign-in* to **Open a pinned
workspace**. The KVM stays behind Cove's sign-in, timeouts and audit log. The
cost is streaming video twice (KVM, then workspace, then thin client), which is
fine for a console. If you'd rather open the KVM directly on the thin client,
add its host under **Extra allowed hosts**. It is then reachable without Cove.

## 9. Private certificates

If Cove's certificate isn't publicly trusted (self-signed, or from your own CA),
point `COVE_THIN_CLIENT_CA_FILE` at a PEM file holding it. The
[LAN TLS setup](deployment.md) (`docker-compose.lan-tls.yml`) already does this
with `certs/cove.crt`. For a private CA, put the CA and the server certificate
in the file. Cove then:

- **Pins the key in the one-liner.** The install command becomes
  `curl -fsSLk --pinnedpubkey "sha256//…" "…/install.sh?code=…" | sudo bash`.
  `-k` only skips the CA check; curl still refuses any server whose key doesn't
  match the pin, so even the first download is verified.
- **Ships the certificate in the install script.** The script adds it to the
  device's trust store before enrolling. The agent also copies it into the kiosk
  browser's own trust store, because Chromium on Linux doesn't read `/etc/ssl`.

For the install USB, pass the same file with `--ca-file certs/cove.crt`.

Without the setting, the installer fails at *Enrolling* with
`CERTIFICATE_VERIFY_FAILED`. The code isn't used up by that failure, so set the
variable, restart Cove, and run the new install command.

## 10. Troubleshooting

| Symptom | Check |
|---|---|
| Install was interrupted (reboot, dropped SSH) | Once enrolled, the agent is enabled before the long package step: reboot and it finishes the setup by itself, or run `python3 /usr/local/lib/cove-thin-client/agent.py install` as root. No new code is needed. |
| Card stays **pending** | The installer never reached `/api/thin-client/enroll`. Run it again with a fresh code and read its output. |
| **offline** | `systemctl status cove-thin-client`, then `journalctl -u cove-thin-client -e`. Is Cove's URL reachable from the device? |
| Kiosk shows **LINK DOWN** | The device can't reach Cove. It retries, and switches to Cove by itself once Cove is reachable. |
| Kiosk shows **DEVICE REVOKED** | The device was deleted or re-enrolled elsewhere. Mint a code and re-run the installer. |
| **BINDING FAILED** | A launch ticket expired or was reused. The agent restarts the browser. If it repeats, check the device clock and the agent log. |
| Black screen, card says *no session on screen* | sway didn't start: `journalctl -t cove-session -b`. Common causes are GPU drivers and a missing `/dev/dri`. |
| Brightness does nothing | The monitor may not support DDC/CI (often a menu option), or the cable or adapter drops it. Run `ddcutil detect` on the device. |
| Need a shell | SSH as `coveadmin` (install USB with `--ssh-key`), or boot GRUB's recovery entry. |

Files on the device: `/etc/cove-thin-client/` (config, token, sway config),
`/var/lib/cove-thin-client/` (browser profile, cached policy), and
`/usr/local/lib/cove-thin-client/agent.py`. Run `agent.py status` to print the
config and the cached policy.

## 11. Testing

`thinclient/test/e2e.py` runs the whole flow in containers: a real Cove image,
and a plain Debian 13 container booting systemd. It runs the install one-liner,
the browser binding, a sign-in typed into the kiosk, the idle screen-off and
sign-out, commands, rotation, an agent self-update that keeps the browser
running, and revocation. sway runs on a headless output there, so it doesn't
test real displays, DDC/CI, or tty1 autologin.

```bash
docker build -t cove:tc-e2e .
python3 thinclient/test/e2e.py --shots /tmp/tc-shots
```

`thinclient/test/iso_vm.py --work DIR` tests the install USB: it builds an image
pointing at a Cove container, installs it into a QEMU VM unattended, boots it,
and waits for the device to enrol and come online. This covers what the
container test can't: the tty1 autologin and sway on a (virtual) display. It is
slow without KVM.

The agent's unit tests run with `cd thinclient && python -m pytest`.
