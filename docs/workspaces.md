# Workspaces

A **workspace** is an on-demand container — a full Linux desktop or a single-app
browser — streamed to your browser. This page is the complete reference for
workspace types, launch options, storage, per-workspace apps, hardening, and the
lifecycle.

## Workspace types

The type comes from the chosen image:

| Type | Examples | Startup URL? |
|---|---|---|
| **Desktop** | Webtop (XFCE/KDE/MATE on Ubuntu/Debian/Arch/Fedora/Alpine), Kali | No |
| **Browser** | Chromium, Brave, Firefox, Edge | Yes — booted at the given URL(s) |
| **Link** (legacy) | custom catalog entries | Yes — required |

All images are LinuxServer.io, Selkies-based, listen on port 3000 internally, and
persist to `/config`. Only browser/link images accept a startup URL; for desktop
images any submitted URL is ignored. Browsers accept **up to 6 URLs** (one tab
each); link workspaces take exactly one.

## Launch options

Set when you click **Deploy Node** / **Open Website**. Defaults shown are what the
form and API use.

| Option | Default | What it does |
|---|---|---|
| **Name** | — | Display name; also sanitized into the storage directory name. Required. |
| **Image** | — | Catalog image to run. Must be enabled. Required. |
| **Target URL(s)** | — | Startup URL(s) for browser/link images; one per line, http/https only, up to 6. |
| **Kiosk mode** | off | Single-URL browser only: full-screen, no chrome (`--kiosk`). |
| **Dark mode** | off | Kiosk only: forces page dark mode. |
| **Allow right-click / refresh menu** | off | Kiosk only: uses `--start-fullscreen` instead of a hard `--kiosk` lock. |
| **Ephemeral** | off | No persistent `/config` mount; all data wiped on halt. (Offered for URL-capable images.) |
| **Route through Tailscale** | off | Egress via a per-workspace Tailscale sidecar. Requires a configured auth key. Mutually exclusive with Gluetun. |
| **Route through Gluetun (VPN)** | off | Egress via a per-workspace VPN sidecar. Requires an uploaded config. One active Gluetun workspace per user. |
| **Custom DNS** + **DNS servers** | off | Use specific resolvers (≤6 IPs) instead of Docker/host DNS. Ignored for Tailscale workspaces. |
| **LAN access** | off | Opt in to direct LAN egress. Only effective if the admin enabled LAN access and configured subnets. |
| **Allow sudo** | off | Permit in-container `sudo`. Overridden if the admin force-disables sudo globally. |
| **Inject SSH key** | on | Copy your account SSH key into `~/.ssh`. No-op if you have no key on file. |
| **Install packages** | — | Distro packages installed at boot (via `universal-package-install`). |
| **proot-apps** | — | LinuxServer proot-apps to install at boot. |
| **AppImages** | — | AppImage URLs to download, extract, and add to the menu. |
| **Tailscale exit node / accept routes / accept DNS** | accept routes & DNS on | Per-launch Tailscale options (Tailscale workspaces only). |

## Persistent vs. ephemeral storage

- **Persistent (default):** the home directory lives on the host at `{COVE_STORAGE_PATH}/{username}/workspace-{name}/`, bind-mounted at `/config`. It survives halt/restart and is reused on relaunch. See [Configuration → Storage](configuration.md#persistent-storage).
- **Ephemeral:** no bind mount — `/config` is in the container's writable layer and is **discarded when the container is removed** (which happens on every halt). Use it for throwaway browsing sessions.

## Per-workspace apps

All three install methods run at container boot via LinuxServer init scripts and
are best-effort (they never fail the boot):

- **Install packages** — adds the `universal-package-install` Docker Mod and installs your distro packages.
- **proot-apps** — installs the named [proot-apps](https://github.com/linuxserver/proot-apps). Installs run **in the background** so the desktop comes up promptly; apps appear in the menu as each finishes (progress logged to `/config/.cove-proot-apps.log`). Already-installed apps are skipped.
- **AppImages** — downloads each URL and, because the containers are hardened (no FUSE), **extracts** it rather than FUSE-mounting, then writes a desktop launcher. Electron apps launch with `--no-sandbox`. Background install, logged to `/config/.cove-appimages.log`.

When packages or proot-apps are requested, the workspace shows a **Provisioning**
screen ("this can take a few minutes") until the desktop is ready.

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

## Lifecycle

| Action | What happens |
|---|---|
| **Launch** | Row created as `creating`; the container starts on its own isolated network (`cove-ws-net-<id>`); flips to `running` once the GUI answers a readiness probe. Slow installs stay `creating` and are promoted later. |
| **Halt / stop** | The container (and any sidecar/network/staged key) is **removed**. Status → `stopped`. Persistent `/config` is kept; ephemeral data is gone. |
| **Start** | Recreates the container reusing the persistent home. **Always pulls the latest image first** (falls back to the local copy if offline), so workspaces stay current. |
| **Clone** | Copies a stopped workspace's entire `/config` into a new workspace (optionally on a different image). The source must be stopped so files are at rest. |
| **Delete / Purge** | Removes the container and the record. With **purge storage**, the persistent home directory is deleted too; without it, the home is left on disk. |
| **Runtime cap** | If the admin set a **max runtime**, running workspaces older than that are auto-stopped. |

## Networking

Each workspace runs on its own isolated network and is **WAN-only by default**.
LAN access, custom DNS, Tailscale, and Gluetun are all per-workspace and covered
in **[Networking & routing](networking.md)**.
</content>
