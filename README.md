<div align="center">
  <img src="frontend/public/favicon.svg" width="96" alt="Cove logo" />
  <h1>Cove</h1>
  <p><strong>Ephemeral desktop & browser containers for your home lab.</strong></p>
  <p>
    A self-hosted, Kasm-style VDI built on
    <a href="https://docs.linuxserver.io/images/docker-webtop/">LinuxServer.io</a>
    webtop images and streamed to the browser via Selkies — fronted by Traefik
    with per-workspace network isolation.
  </p>
  <p>
    <img src="https://img.shields.io/badge/license-GPLv3-blue.svg" alt="License: GPLv3" />
    <img src="https://img.shields.io/badge/backend-FastAPI%20%C2%B7%20Python%203.14-009688.svg" alt="Backend: FastAPI / Python 3.14" />
    <img src="https://img.shields.io/badge/frontend-Vue%203%20%C2%B7%20TypeScript-42b883.svg" alt="Frontend: Vue 3 / TypeScript" />
    <img src="https://img.shields.io/badge/stream-Selkies-ff2e88.svg" alt="Streaming: Selkies" />
  </p>
</div>

<p align="center">
  <img src="screenshots/cove-launch.gif" width="90%" alt="Launching a workspace in Cove: Launch, pick Desktop, search for XFCE, name it, then the Starting screen gives way to a full Linux desktop streamed into the browser" />
</p>

---

Cove lets you spin up full Linux desktops (XFCE, KDE, MATE, i3 on
Ubuntu/Debian/Arch/Fedora/Alpine), security desktops (Kali), single applications
(VSCodium, Blender, GIMP, Krita, and more), and single-app browsers (Chromium,
Brave, Firefox) **on demand** — then open them in any browser tab, with no client
to install. It's built for home labs: simple to run, low overhead, multi-user.

**[Quick start](#quick-start) · [Features](#features) · [Documentation](docs/) · [Architecture deep-dive](ARCH.md)**

## Screenshots

<table>
  <tr>
    <td width="50%" align="center">
      <img src="screenshots/dashboard.png" alt="The Cove dashboard: a running Linux desktop with a live screen preview, CPU/memory, uptime and auto-stop countdown, and two stopped workspaces routed through a Tailscale profile and a VPN profile" /><br />
      <sub><b>Dashboard</b> — live previews, CPU/memory, uptime and auto-stop, and each workspace's route at a glance</sub>
    </td>
    <td width="50%" align="center">
      <img src="screenshots/desktop-stream.png" alt="A full Linux XFCE desktop streamed into the browser, with Cove's stream bar on top" /><br />
      <sub><b>Desktops</b> — full XFCE/KDE/MATE/i3 sessions in a tab</sub>
    </td>
  </tr>
  <tr>
    <td width="50%" align="center">
      <img src="screenshots/vpn-profiles.png" alt="Preferences: a list of VPN profiles (two Mullvad keys, a Proton VPN login and an incomplete config-file profile) and Tailscale profiles, each with its status and which workspaces use it" /><br />
      <sub><b>VPN &amp; Tailscale profiles</b> — several keys, providers or tailnets, each showing whether it's ready and where it's used</sub>
    </td>
    <td width="50%" align="center">
      <img src="screenshots/vpn-profile-editor.png" alt="Editing a VPN profile set up from a built-in provider: Mullvad over WireGuard with a stored key, interface address, and a country and city to connect to" /><br />
      <sub><b>Built-in VPN providers</b> — pick Mullvad, Proton, AirVPN…, paste the key, choose where to connect. No config file needed</sub>
    </td>
  </tr>
  <tr>
    <td width="50%" align="center">
      <img src="screenshots/wizard.png" alt="The New Workspace wizard's network step, choosing which saved VPN profile the workspace routes through" /><br />
      <sub><b>New Workspace wizard</b> — choose Direct, Tailscale or a VPN, and which profile, per workspace</sub>
    </td>
    <td width="50%" align="center">
      <img src="screenshots/image-registry.png" alt="The admin image registry: per-image download state and size, last pull, how many workspaces use it, and a row menu with Check for update, Disable, Edit and Delete" /><br />
      <sub><b>Image registry</b> — what's downloaded, how big, who uses it, and update checks</sub>
    </td>
  </tr>
  <tr>
    <td width="50%" align="center">
      <img src="screenshots/thin-clients.png" alt="Admin, Thin clients: three kiosk screens, one waiting to enrol and two online with live screenshots, session, screen, display, audio, OS updates and agent version" /><br />
      <sub><b>Thin clients</b> — every kiosk screen at a glance, with screenshots and one-click sleep, sign-out and settings</sub>
    </td>
    <td width="50%" align="center">
      <img src="screenshots/integrations.png" alt="Admin, Integrations: Home Assistant tokens with Control or Read access, when each was created and last used, and a Revoke button" /><br />
      <sub><b>Integrations</b> — scoped tokens for Home Assistant, separate from any sign-in</sub>
    </td>
  </tr>
  <tr>
    <td colspan="2" align="center">
      <img src="screenshots/files.png" width="50%" alt="The Files page with a row's ⋯ menu open: Download, Copy, Cut, Move to trash and Delete permanently" /><br />
      <sub><b>Files</b> — browse, upload and organise your storage; per-row actions in a ⋯ (or right-click) menu</sub>
    </td>
  </tr>
</table>

## Features

### 🖥️ Workspaces

- **One-click launch** — spin up a desktop or browser container and stream it straight to your browser.
- **One launcher** — a single flow asks what you want (desktop, browser or app), then shows only images of that kind. Browsers can boot straight at up to six URLs, kiosk-style for web-app delivery, with dark mode and full-screen variants offered only where that browser honours them.
- **Seven browsers** — Chromium, Brave, Firefox, Edge, Helium, Vivaldi and Opera.
- **In-stream controls** — fullscreen, a CRT toggle, HALT, and a **quick-switch menu** to jump between (or boot) other nodes without leaving the stream.
- **One screen at a time** — opening a workspace on another browser or a thin client moves the stream there, and the page that had it says where it went, with **Take it back here**.
- **Live dashboard** — workspaces split into Active/Offline, with per-container **CPU & memory** on running cards and the **Tailscale IP** shown (and copyable) for tailnet nodes.
- **Screen previews** — every running card shows a still of what's actually on that node's screen, captured from its own stream (X11 and Wayland alike). Doubles as the readiness check: a node isn't "running" until its stream renders a real frame. Previews are dropped the moment a workspace halts, and a workspace can't be connected to until its stream has rendered that first frame.
- **Per-workspace apps** — install distro packages (`universal-package-install`), LinuxServer **proot-apps**, and **AppImages** (auto-extracted with a desktop launcher) at launch, then **manage them from the UI**: see what's installed, which proot-apps have a newer build, update/install/remove either kind, and watch it run in a **background tasks** menu.
- **Fresh containers** — halting a workspace removes its container; bringing it back always pulls the latest image.
- **Persistent storage** — per-workspace home directories that survive restarts (or go fully ephemeral, wiped on halt).
- **Thin clients** — turn a spare mini PC into a kiosk that boots straight into Cove (Debian, sway and locked-down Chromium), set up with a one-liner or an install USB. Manage every screen from **Admin → Thin clients**: screen-off and on-hours, idle sign-out that ends only that screen's session, auto-open the running or a pinned workspace, rotation and brightness, screenshots, OS updates and reboots. Devices only dial out, so no port is opened, and their agent updates itself from Cove. See [Thin clients](docs/thin-clients.md).
- **Home Assistant integration** — Cove, its zones and its thin clients as Home Assistant devices: user and workspace counts, per-zone workspace CPU and memory, who is signed in on each screen and what it shows, plus switches for screen, mute and keep-awake, a volume slider, and identify, sign-out and reboot buttons. Scoped integration tokens from **Admin → Integrations**; Home Assistant polls over Cove's usual HTTPS address. See [Home Assistant](docs/home-assistant.md).
- **Browser extension** — *Open in Cove* sends any link from your own Chrome-based browser into a disposable Cove browser workspace. Served by your own server from the **user menu → Browser extension**, no store or internet needed.

### 🌐 Networking & privacy

- **Per-user Tailscale routing** — opt a workspace into a per-workspace [Tailscale](https://tailscale.com/) sidecar using your own preauth key, with exit-node selection, accept-routes/DNS, and a custom control server.
- **Per-user VPN via Gluetun** — route a workspace's egress through a per-workspace [Gluetun](https://github.com/qdm12/gluetun) sidecar. Keep several VPN profiles, each from an uploaded OpenVPN/WireGuard config or one of Gluetun's built-in providers (credentials stored encrypted), and pick one per workspace; each profile is connected to one workspace at a time.
- **Custom DNS** — point a (non-VPN) workspace at public resolvers (e.g. `1.1.1.1`, `9.9.9.9`) instead of local DNS.
- **Egress policy** — workspaces are WAN-only by default. Docker-internal and cloud-metadata ranges are *always* blocked (so workspaces can never reach the Cove backend, the socket proxy, or each other); admins can allow specific LAN subnets that a workspace opts into per launch. Tailscale workspaces keep tailnet/subnet/exit-node access while raw-bridge egress stays firewalled.
- **Optional subdomain isolation** — set `COVE_WORKSPACE_DOMAIN` to stream each workspace from its own origin (`{id}.domain`) so it can't reach the SPA's token; unset falls back to subpath routing.

### 🛡️ Security

- **Authentication** — local accounts (bcrypt) *and* OIDC/SSO against any standards-compliant provider (Kanidm, Authentik, Keycloak, Entra ID, Okta, Google), with an optional **OIDC-only** mode that disables local login. Password management is hidden for SSO accounts.
- **Defense in depth** — ForwardAuth-gated streams, per-workspace isolated Docker networks, split read-only/write Docker socket proxies, verified OIDC tokens, dropped capabilities, short-lived JWTs with refresh, real-client-IP rate limiting, audit logging, and optional at-rest DB encryption.

### ⚙️ Admin & catalog

- **Auto-populated catalog** — images are pulled from the [LinuxServer.io API](https://docs.linuxserver.io/API/) on first run and via one-click admin sync; logos auto-fetched.
- **Manual image pulls** — pull/re-pull catalog images from the admin UI with live download status; delete the local image only, or the catalog entry too.
- **Resource limits** — admin-set default **CPU (cores)** and **memory (MB)** caps applied to workspace containers (0 = unlimited).
- **Admin settings** — pin/override the Tailscale and Gluetun sidecar images, toggle LAN access, force-disable sudo, set max runtime and CPU/memory limits, plus a read-only summary of env-configured settings.
- **Admin UI** — manage users, images, live sessions, and the audit log.
- **Per-user zone limits** — keep a zone (say, a LAN box) for some accounts: a limited account sees, launches on and stores files to only the zones you tick.

### ✨ Experience

- **File browser** — browse, upload, download, and delete files in your workspace storage areas.
- **User preferences** — a self-service page to change your password and manage Tailscale settings.
- **Installable PWA** — add Cove to your home screen / desktop; offline-aware app shell (the live stream and API are never cached).
- **Cyberpunk UI** — neon theme with an optional CRT toggle on the stream.

## Quick start

```bash
git clone <your-fork-url> cove && cd cove
cp .env.example .env
docker compose up --build -d
```

Open <http://localhost>, complete first-run admin setup, and launch a workspace.
Full instructions — including HTTPS, OIDC, DNS-01, and storage — are in
**[SETUP.md](SETUP.md)**.

## Architecture

```
                         ┌────────────────────────────────────────┐
  browser ──HTTP/S──▶ Traefik ──▶ cove (FastAPI + Vue SPA)         │
                         │  └─ForwardAuth─▶ /api/auth/forward       │
                         │                                          │
                         ├──▶ workspace container (webtop/browser)  │  each on its own
                         │      isolated network, port 3000         │  cove-ws-net-<id>
                         │                                          │
   cove ──┐              └──▶ docker-socket-proxy ──▶ /var/run/docker.sock (filtered)
          └─ manages containers via the proxy (DOCKER_HOST)
```

- **Backend** — Python 3.14, FastAPI, SQLAlchemy (SQLite/WAL), Docker SDK.
- **Frontend** — Vue 3 + TypeScript + Vite + Pinia.
- **Proxy** — Traefik v3 (label-based, auto-routes each workspace; TLS via Let's Encrypt, TLS-ALPN or DNS-01).
- **Workspaces** — `lscr.io/linuxserver/*` images (port 3000, `/config`).

A full breakdown — runtime topology, auth/stream flows, the workspace lifecycle,
and the data model — is in **[ARCH.md](ARCH.md)**.

## Testing

```bash
# Backend (from backend/, in a venv): ruff + pytest
ruff check server && pytest -q

# Frontend (from frontend/): typecheck + Vitest
npx vue-tsc --noEmit && npm test
```

CI runs lint, both test suites, a frontend build, and a Docker image build (see `.gitlab-ci.yml`).

## Built on / acknowledgements

Cove is glue around excellent open-source projects — all credit to their authors and maintainers:

- **[LinuxServer.io](https://www.linuxserver.io/)** ([images](https://docs.linuxserver.io/)) — the webtop/desktop & browser container images, the `universal-package-install` Docker mod, and [proot-apps](https://github.com/linuxserver/proot-apps). Workspaces stream via **[Selkies](https://github.com/selkies-project/selkies)** (the LinuxServer images Cove ships are all Selkies-based; **[KasmVNC](https://github.com/kasmtech/KasmVNC)** images also work).
- **[Tailscale](https://tailscale.com/)** ([tailscale/tailscale](https://github.com/tailscale/tailscale)) — the per-workspace tailnet routing sidecar.
- **[Gluetun](https://github.com/qdm12/gluetun)** by Quentin McGaw ([@qdm12](https://github.com/qdm12)) — the per-workspace VPN (OpenVPN/WireGuard) sidecar.
- **[Traefik](https://traefik.io/)** (Traefik Labs) — reverse proxy, ForwardAuth, and ACME/Let's Encrypt TLS.
- **[tecnativa/docker-socket-proxy](https://github.com/Tecnativa/docker-socket-proxy)** — the filtered Docker API proxy.
- **[netshoot](https://github.com/nicolaka/netshoot)** by Nicola Kabar — the short-lived helper used to apply per-workspace egress firewall rules.
- **WireGuard** is a registered trademark of Jason A. Donenfeld.
- Built with **[FastAPI](https://fastapi.tiangolo.com/)**, **[SQLAlchemy](https://www.sqlalchemy.org/)**, **[Vue](https://vuejs.org/)**, **[Vite](https://vite.dev/)**, **[Pinia](https://pinia.vuejs.org/)**, and **[lucide](https://lucide.dev/)** icons.

These projects are independent of Cove and are not affiliated with it; trademarks belong to their respective owners.

## A note on AI assistance

Cove was built with substantial help from AI coding tools (Claude). AI was used
for writing, refactoring, and reviewing code throughout the project. Every change
is human-reviewed before it lands, but you should evaluate the code on its own
merits — read it, test it, and decide whether it fits your needs before relying
on it. Contributions are welcome regardless of how they're authored.

## License

Cove is licensed under the **GNU General Public License v3.0** — see [LICENSE](LICENSE).
This follows the licensing of the upstream [LinuxServer.io](https://www.linuxserver.io/)
images Cove builds on. LinuxServer.io is not affiliated with this project.
