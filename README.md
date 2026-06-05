<div align="center">
  <img src="frontend/public/favicon.svg" width="96" alt="Cove logo" />
  <h1>Cove</h1>
  <p><strong>Ephemeral desktop & browser containers for your home lab.</strong></p>
  <p>A self-hosted, Kasm-style VDI built on <a href="https://docs.linuxserver.io/images/docker-webtop/">LinuxServer.io</a> webtop images and streamed to the browser via KasmVNC, fronted by Traefik with per-workspace network isolation.</p>
</div>

---

## What it does

Cove lets you spin up full Linux desktops (XFCE, KDE, MATE, i3 on Ubuntu/Debian/Arch/Fedora/Alpine), security desktops (Kali), and single-app browsers (Chromium, Brave, Firefox) on demand, then open them in any browser tab — no client to install. It's aimed at home labs: simple to run, low overhead, multi-user.

## Features

- **One-click workspaces** — launch a desktop or browser container and stream it to your browser.
- **Open-a-website flow** — paste a URL, pick a browser, and Cove boots a kiosk-style browser pointed at it (web-app delivery).
- **Auto-populated catalog** — images are pulled from the [LinuxServer.io API](https://docs.linuxserver.io/API/) on first run and via a one-click admin sync.
- **Authentication** — local accounts (bcrypt) *and* OIDC/Authentik SSO when configured.
- **Per-user Tailscale routing** — opt a workspace into routing through a per-workspace Tailscale sidecar using your own preauth key, with exit-node selection, accept-routes/DNS, and a custom login (control) server.
- **User preferences** — a self-service page to change your password and manage Tailscale settings.
- **File browser** — browse, upload, download, and delete files in your workspace storage areas.
- **Fresh containers** — halting a workspace removes its container; bringing it back always pulls the latest image.
- **Installable PWA** — add Cove to your home screen / desktop; offline-aware app shell (the live stream and API are never cached).
- **Security-first** — ForwardAuth-gated streams, per-workspace isolated Docker networks, a filtered Docker socket proxy, dropped capabilities, short-lived JWTs with refresh, audit logging, and optional at-rest DB encryption.
- **Persistent storage** — per-workspace home directories that survive restarts.
- **Admin UI** — manage users, images, live sessions, and the audit log.
- **Cyberpunk UI** — neon theme with an optional CRT toggle.

## Testing

```bash
# Backend (from backend/, in a venv): ruff + pytest
ruff check server && pytest -q

# Frontend (from frontend/): typecheck + Vitest
npx vue-tsc --noEmit && npm test
```

CI runs lint, both test suites, a frontend build, and a Docker image build (see `.gitlab-ci.yml`).

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

- **Backend** — Python 3.12, FastAPI, SQLAlchemy (SQLite/WAL), Docker SDK.
- **Frontend** — Vue 3 + TypeScript + Vite + Pinia.
- **Proxy** — Traefik v3 (label-based, auto-routes each workspace; TLS via Let's Encrypt, TLS-ALPN or DNS-01).
- **Workspaces** — `lscr.io/linuxserver/*` images (port 3000, `/config`).

## Quick start

```bash
git clone <your-fork-url> cove && cd cove
cp .env.example .env
docker compose up --build -d
```

Open <http://localhost>, complete first-run admin setup, and launch a workspace. Full instructions — including HTTPS, OIDC, DNS-01, and storage — are in **[SETUP.md](SETUP.md)**.

## License

Cove is licensed under the **GNU General Public License v3.0** — see [LICENSE](LICENSE). This follows the licensing of the upstream [LinuxServer.io](https://www.linuxserver.io/) images Cove builds on. LinuxServer.io is not affiliated with this project.
