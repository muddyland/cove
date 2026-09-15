# Workspaces

A **workspace** is an on-demand container — a full Linux desktop, a single
application, or a browser — streamed to your browser. This page is the complete
reference for workspace types, launch options, storage, per-workspace apps,
hardening, and the lifecycle.

## Workspace types

The type comes from the chosen image:

| Type | Examples | Startup URL? |
|---|---|---|
| **Desktop** | Webtop (XFCE/KDE/MATE on Ubuntu/Debian/Arch/Fedora/Alpine), Kali | No |
| **App** | Single-application Selkies GUI images — VSCodium, Blender, GIMP, Krita, Inkscape, Audacity, Calibre, digiKam, Obsidian, Kdenlive, FreeCAD, darktable | No |
| **Browser** | Chromium, Brave, Firefox, Edge | Yes — booted at the given URL(s) |
| **Link** (legacy) | custom catalog entries | Yes — required |

All images are LinuxServer.io, Selkies-based, and persist to `/config`. They serve
their web GUI on an HTTP port that varies per image (webtops/browsers use 3000,
some apps default elsewhere — e.g. Calibre on 8080); Cove forces each image onto
its configured internal port (default 3000) via the baseimage `CUSTOM_PORT`
override, so every image is reachable by the readiness probe and the stream
router regardless of its default. Only browser/link images accept a startup URL;
for other images any submitted URL is ignored. Browsers accept **up to 6 URLs**
(one tab each); link workspaces take exactly one.

## Launch options

Set in the **New Workspace** wizard — *Choose* (image) → *Set up* (name/URL) →
optional *Network* / *Access* / *Apps* → *Review* — or via the API. A first
launch only needs a name and an image; everything else has a safe default. The
*Apps* step is shown for desktop and app images; its proot-apps and AppImages
fields for desktops only, since they add launchers to a desktop menu that a
single-app image doesn't have.

| Option | Default | What it does |
|---|---|---|
| **Name** | — | Display name; also sanitized into the storage directory name. Required. |
| **Image** | — | Catalog image to run. Must be enabled. Required. |
| **Target URL(s)** | — | Startup URL(s) for browser/link images; one per line, http/https only, up to 6. |
| **Kiosk mode** | off | Single-URL browser only: full-screen, no chrome (`--kiosk`). |
| **Dark mode** | off | Kiosk only: forces page dark mode. |
| **Allow right-click / refresh menu** | off | Kiosk only: uses `--start-fullscreen` instead of a hard `--kiosk` lock. |
| **Ephemeral** | off | No persistent `/config` mount; all data wiped on halt. (Offered for URL-capable images.) |
| **Discard when stopped** | off | `docker run --rm` semantics: the workspace *record* is deleted once its container stops, so the card leaves the grid instead of lingering as one that can only ever start blank. **Requires Ephemeral** — it is rejected on a persistent workspace, whose saved home would be destroyed by an ordinary Halt (that is Purge's job, and Purge asks). Applies to the admin **max runtime** auto-stop too. |
| **Shared profile** | off | Mount one per-user home — `{COVE_STORAGE_PATH}/{username}/_profile/` — as `/config` on *every* one of your shared-profile workspaces, so dotfiles, proot-apps, browser profiles and VSCodium workspaces carry between distros. The profile belongs to you, not the workspace: it is **never** deleted when a workspace is purged. Best used one workspace at a time — concurrent desktops on one home hit profile/browser locks (see **Clear stale browser lock**). Turning it on for an existing workspace switches its home; the old per-workspace files stay on disk. **Ephemeral** wins if both are set — an ephemeral workspace gets no bind mount at all. |
| **Route through Tailscale** | off | Egress via a per-workspace Tailscale sidecar. Requires a configured auth key. Mutually exclusive with Gluetun. |
| **Route through Gluetun (VPN)** | off | Egress via a per-workspace VPN sidecar. Requires an uploaded config. One active Gluetun workspace per user. |
| **Custom DNS** + **DNS servers** | off | Use specific resolvers (≤6 IPs) instead of Docker/host DNS. Ignored for Tailscale workspaces. |
| **LAN access** | off | Opt in to direct LAN egress. Only effective if the admin enabled LAN access and configured subnets. |
| **Allow sudo** | off | Permit in-container `sudo`. Overridden if the admin force-disables sudo globally. |
| **Inject SSH key** | on | Copy your account SSH key into `~/.ssh`. No-op if you have no key on file. |
| **Wayland streaming** | on | Stream over Wayland (`PIXELFLUX_WAYLAND=true`) — Smithay plus labwc. Turn off to force the X11/Xvfb fallback. Required for GPU hardware encode. |
| **GPU acceleration** | off | Hardware VAAPI video encode on the host GPU. Requires the admin GPU toggle **and** Wayland streaming. See [GPU acceleration](#gpu-acceleration). |
| **Clear stale browser lock** | off | URL-capable images only: at boot, remove a leftover single-instance lock (`SingletonLock`/`SingletonCookie`/`SingletonSocket` for the Chromium family, `lock`/`.parentlock` for Firefox) from the saved `/config` profile. An unclean halt leaves one behind and the browser then exits on the next boot — the desktop streams but no browser appears. Only lock files are removed, never profile data. |
| **Docker (dev)** | off | Run `docker` inside the workspace via a privileged nested daemon. Desktop and app workspaces on the local zone only; requires the admin Docker toggle. |
| **Install packages** | — | Distro packages installed at boot (via `universal-package-install`). Desktop and app workspaces. |
| **proot-apps** | — | LinuxServer proot-apps to install at boot. Desktops only. |
| **AppImages** | — | AppImage URLs to download, extract, and add to the menu. Desktops only. |
| **Tailscale exit node / accept routes / accept DNS** | accept routes & DNS on | Per-launch Tailscale options (Tailscale workspaces only). |

## Persistent vs. ephemeral storage

- **Persistent (default):** the home directory lives on the host at `{COVE_STORAGE_PATH}/{username}/workspace-{name}/`, bind-mounted at `/config`. It survives halt/restart and is reused on relaunch. See [Configuration → Storage](configuration.md#persistent-storage).
- **Ephemeral:** no bind mount — `/config` is in the container's writable layer and is **discarded when the container is removed** (which happens on every halt). Use it for throwaway browsing sessions.
- **Shared profile:** one home per *user* at `{COVE_STORAGE_PATH}/{username}/_profile/`, mounted as `/config` on every workspace that has the option on. Such a workspace has no home of its own, and the shared profile is left alone when the workspace is purged.

## Per-workspace apps

All three install methods run at container boot via LinuxServer init scripts and
are best-effort (they never fail the boot):

- **Install packages** — adds the `universal-package-install` Docker Mod and installs your distro packages.
- **proot-apps** — installs the named [proot-apps](https://github.com/linuxserver/proot-apps). Installs run **in the background** so the desktop comes up promptly; apps appear in the menu as each finishes (progress in the navbar's tasks menu, and appended to `/config/.cove-proot-apps.log`). Already-installed apps are skipped — booting never updates them; see below.
- **AppImages** — downloads each URL and, because the containers are hardened (no FUSE), **extracts** it rather than FUSE-mounting, then writes a desktop launcher. Electron apps launch with `--no-sandbox`. Background install, logged to `/config/.cove-appimages.log`.

When packages or proot-apps are requested, the workspace shows a **Provisioning**
screen ("this can take a few minutes") until the desktop is ready.

### Managing proot-apps in a running workspace

**Actions → Apps** on a running desktop (or **Apps** in the stream page's menu)
lists the proot-apps installed in it (with each app's icon from the LinuxServer
catalog) and whether each is **up to date** or has an
**update available**. Cove compares the build each app was installed from with the
one ghcr.io serves now — the same check `proot-apps update` makes — without
downloading anything. Results are cached for about half an hour; only apps from
the LinuxServer catalog are checked.

From there you can **Update** one app or **Update all**, **Install** more, or
**Remove** one. Installing or removing also updates the workspace's saved
proot-apps list, so what you see survives a restart and a migration (migration
reinstalls from that list rather than copying the app files).

Each of these runs as a **background task** inside the workspace, one at a time
(at most three waiting or running at once). The **tasks menu** in the navbar (the
checklist icon, a spinner with a count while something runs) shows every task
across your running workspaces, with progress and the full log. Tasks live inside
the container, so a halt clears them; a task cut short by a restart shows as
**interrupted**.

An update deletes the old copy before downloading the new one — that's how
proot-apps works — so if the download fails, the app is left uninstalled. Install
it again from the same dialog (it's still in the saved list, so the next boot
retries it too).

## SSH-key injection

If you have an SSH key on file (Preferences) and **Inject SSH key** is on, your
key is staged and copied into `/config/.ssh` at boot with strict permissions
(dir `700`, private `600`, public `644`) and owned by the desktop user — so
in-container `git`/`ssh` works immediately. The staged copy is removed when the
workspace stops. Turn the toggle off to skip injection for a given workspace.

## Desktop username

Inside the container, `whoami`, the shell prompt, and `ls -l` show **your Cove
username** instead of the image's default `abc` user. This is a cosmetic alias
(same UID and `/config` home) that leaves the LinuxServer services — which
reference `abc` by name — working. Reserved names (`abc`, `root`) are skipped.

## Sudo & container hardening

Every workspace container runs hardened:

- All Linux capabilities are dropped, then a minimal set is added back (`CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `SETGID`, `SETUID`, `KILL`).
- `no-new-privileges` is applied when the **admin force-disables sudo** *or* the workspace did **not** request sudo. So:
  - **Allow sudo on** + admin setting off → sudo works.
  - **Admin force-disable sudo on** → sudo is blocked everywhere, regardless of the per-workspace toggle.
- Admin CPU/memory caps are applied at start when configured (see [Administration → Settings](administration.md#settings)).

## GPU acceleration

When enabled, the workspace encodes its video stream in hardware (**VAAPI** on the
host GPU) instead of CPU `x264`, offloading the stream for smoother, lower-latency
desktops. It applies to desktop/app workspaces.

**Requirements (all must hold, or it's rejected/ignored):**

- The admin **GPU acceleration** master toggle is on (Administration → Settings).
- **Wayland streaming** is on — hardware encode needs it. Enabling GPU with
  Wayland off is rejected at create/edit with a clear error.
- The workspace's host has a usable GPU with a DRI render node.

**Render node & group (auto-detected).** Cove bind-mounts the host's DRI render
node (default `/dev/dri/renderD128`) and adds the workspace user to the node's
group so it can open the device. The render group's GID **varies per host** (e.g.
`990`, `992`, `44`, `993`), and a wrong GID silently breaks VAAPI — the classic
"GPU on ⇒ stutter" failure. So at launch Cove **probes the actual device on the
host and uses its real group**, per zone; the admin `render node` / `render GID`
settings are only an override/fallback. On a multi-GPU host, point the render-node
setting at the correct card.

**Failures are surfaced, not silent.** If GPU acceleration is on but the host has
no render node at the configured path, the workspace goes to **error** with a
message like *"no render node exists at /dev/dri/renderD128 on this host's GPU"*,
shown on the card and the workspace view — instead of launching a broken,
software-thrashing stream.

**Troubleshooting stutter.** With auto-detection the common GID mismatch is fixed
automatically. If a GPU workspace still stutters: confirm the host GPU isn't
oversubscribed by several concurrent GPU workspaces (they share one encoder), and
verify on the host that the encoder is actually engaged (`vainfo`, and
`radeontop`/`intel_gpu_top` while streaming). On a low-power shared iGPU, software
encode (GPU off) can genuinely be the smoother choice.

## Resource usage

A running workspace's toolbar has a **gauge** icon next to the **Online** badge.
Click it for a live **CPU** and **memory** readout (usage vs. the container's
limit), refreshed every few seconds. CPU is shown as the container's share of
the whole host, on a 0-100% scale. Admins can also cap per-workspace CPU/memory
in Administration → Settings.

## Screen previews

Cards in the workspace grid show a still of what's on that node's screen.

The frame is captured **once, when the workspace comes up**, from the workspace's
own Selkies stream — so it works the same for X11 and Wayland workspaces, and
needs nothing installed in the image. It doubles as the readiness signal: a frame
Cove can actually decode proves the stream, compositor and encode pipeline are all
working, which an HTTP 200 from the container's web server does not.

You can't connect to a workspace until that first frame exists — the card shows
**STARTING** and the stream page **Starting desktop** instead. Opening the stream
before Selkies is drawing leaves a client that only a page reload recovers. If the
first frame hasn't arrived by the time the workspace goes running, Cove keeps
trying in the background; an image whose stream it can't capture at all opens
anyway about 90 seconds later.

Privacy and lifecycle:

- The launch frame is stored on the server (in the database, so it inherits
  at-rest encryption if `COVE_DB_ENCRYPTION_KEY` is set).
- It is **deleted when the workspace halts, errors, or is purged** — a stopped
  node never shows what was last on its screen. Booting it again takes a fresh one.
- Previews are served with `private` caching only, and only to the workspace's
  owner (or an admin).
- Capturing **never disturbs a live session.** Cove takes a frame the stream is
  already broadcasting; it only asks the stream to start when nobody is connected,
  because becoming the stream's primary client would disconnect whoever is
  watching. On-demand refreshes never do this at all.

A workspace whose stream Cove can't read simply shows its project logo instead —
it still launches normally.

## Workspace icons

A workspace is normally marked with the logo of the image it runs — the Chromium
logo for a Chromium node, the Firefox one for Firefox.

A **website workspace opening a single site** is marked with **that site's own
favicon** instead, on its card, in the toolbar of the open node, and on the home
screen when you install it as an app. A node that only ever opens Home Assistant
reads as Home Assistant, which is how you think of it.

- Cove fetches the icon itself, shortly after launch (and again whenever you
  change the URL), so it works for plain-`http` sites on your LAN, which a
  browser would refuse to load onto an `https` page.
- The icon is stored with the workspace and served only to its owner.
- **Several URLs → the browser logo.** With more than one site open, no single
  site speaks for the workspace.
- Anything Cove can't fetch or decode — a site that's down, or one whose only
  favicon is an SVG — also falls back to the browser logo. Nothing else changes.
- Cove won't fetch icons from loopback or link-local addresses; sites on private
  LAN ranges are fine.

## When the server is unreachable

If the interface can't reach Cove's backend at all — it's restarting, the machine
is down, or your connection to it dropped — the page is taken over by an
**offline** screen rather than leaving you with buttons that do nothing.

It retries on its own (quickly at first, then backing off) and clears itself as
soon as the server answers, so a restart resolves without you doing anything.
There's a **Retry** button if you'd rather not wait. This is about reaching the
server: an error *from* the server shows as an ordinary message instead.

## Lifecycle

| Action | What happens |
|---|---|
| **Launch** | Row created as `creating`; the container starts on its own isolated network (`cove-ws-net-<id>`); flips to `running` once the stream renders a frame Cove can decode (falling back to the plain HTTP probe if it can't). Slow installs stay `creating` and are promoted later. |
| **Halt / stop** | The container (and any sidecar/network/staged key) is **removed**. Status → `stopped`. Persistent `/config` is kept; ephemeral data is gone. The stored screen preview is deleted. A **Discard when stopped** workspace has no `stopped` state — its record is deleted here instead. |
| **Start** | Recreates the container reusing the persistent home. **Always pulls the latest image first** (falls back to the local copy if offline), so workspaces stay current. |
| **Clone** | Copies a stopped workspace's entire `/config` into a new workspace (optionally on a different image). The source must be stopped so files are at rest. |
| **Delete / Purge** | Removes the container and the record. With **purge storage**, the persistent home directory is deleted too; without it, the home is left on disk. A **shared profile** is never purged this way. |
| **Runtime cap** | If the admin set a **max runtime**, running workspaces older than that are auto-stopped. |

## Networking

Each workspace runs on its own isolated network and is **WAN-only by default**.
LAN access, custom DNS, Tailscale, and Gluetun are all per-workspace and covered
in **[Networking & routing](networking.md)**.
