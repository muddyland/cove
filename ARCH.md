# Cove — Architecture

This document explains how Cove is put together: the runtime topology, the
request/auth flows, the data model, and how a workspace container is born, lives,
and dies. For installation and configuration see **[SETUP.md](SETUP.md)**; for a
feature overview see **[README.md](README.md)**.

---

## 1. The big picture

Cove is a single Python service (FastAPI) that also serves a compiled Vue SPA,
sitting behind Traefik. It talks to the Docker daemon **only** through a filtered
socket proxy, and it launches each user workspace as its own LinuxServer.io
container on a per-workspace isolated network.

```
                              ┌───────────────────────────────────────────────┐
                              │ Docker host                                    │
                              │                                                │
   browser ──HTTP/S──▶ ┌──────┴─────┐                                          │
                       │  Traefik   │  :80/:443, label-based routing, ACME TLS  │
                       │  (v3.2)    │                                          │
                       └──┬───┬───┬─┘                                          │
        ForwardAuth ◀─────┘   │   └──────────────▶ workspace containers        │
        /api/auth/forward     │                    (webtop / browser)          │
                              ▼                     each on cove-ws-net-<id>    │
                       ┌────────────┐  :8080                ▲                   │
                       │   cove     │  FastAPI + Vue SPA     │ create/stop/exec  │
                       │            │───────────────────────┘ (DOCKER_HOST)     │
                       └──┬─────────┘                                           │
                          │ tcp://docker-socket-proxy:2375                      │
                          ▼                                                     │
                   ┌──────────────────┐   read-only proxy ──▶ Traefik discovery │
                   │ docker-socket-   │   write proxy ──────▶ cove only         │
                   │ proxy (x2)       │──▶ /var/run/docker.sock (filtered)      │
                   └──────────────────┘                                         │
                              └────────────────────────────────────────────────┘
```

### Containers in the base stack (`docker-compose.yml`)

| Service | Role |
|---|---|
| `traefik` | Reverse proxy. Routes `/` and `/api/*` to cove, auto-discovers each workspace via Docker labels, terminates TLS, runs the ForwardAuth + errors + rate-limit + security-headers middlewares. Uses the **read-only** socket proxy for discovery. |
| `cove` | The app: FastAPI backend + the built SPA (served from `/app/static`). Manages workspaces via the **write** socket proxy. |
| `docker-socket-proxy` | Write-capable filtered Docker API (`CONTAINERS/IMAGES/NETWORKS/VOLUMES/POST/EXEC=1`). Reachable only by cove on the internal `cove-sockwrite` network. |
| `docker-socket-proxy-ro` | Read-only filtered Docker API for Traefik discovery (no `POST`). |
| workspace containers | Created at runtime, one per running workspace. Not declared in compose. |
| tailscale sidecars | Created at runtime for Tailscale-routed workspaces (`cove-ts-<id>`). |

Two socket proxies enforce least privilege: a Traefik compromise (read-only)
cannot create containers, and only cove (write) can — so neither path is a direct
host-root pivot via the raw socket.

### Networks

- `cove-net` — Traefik ↔ cove ↔ read-only proxy, and where Traefik is attached
  to each workspace network for routing.
- `cove-sockwrite` — internal (no external connectivity); cove ↔ write proxy only.
- `cove-ws-net-<id>` — one isolated bridge per workspace, created at launch.
  IPv6 disabled (the egress guard is IPv4-only). Traefik is connected to it so it
  can reach the stream; the workspace cannot reach cove, the proxies, or peers.

---

## 2. Components

### Backend (`backend/server/`)

Python 3.12, FastAPI, SQLAlchemy 2.0 over SQLite (WAL), the Docker SDK, and
python-jose for JWTs. Key modules:

| Module | Responsibility |
|---|---|
| `main.py` | App factory, lifespan (migrations, OIDC discovery, catalog seed, status monitor), SPA static serving, the stream-error page. |
| `config.py` | Env-driven `Settings` (prefix `COVE_`), cached. Token lifetimes, cookie names, subdomain/OIDC config, storage path. |
| `settings_store.py` | **Runtime**, admin-editable settings in the `app_setting` table (Tailscale image, LAN access, force-no-sudo, max runtime, CPU/memory caps). Distinct from env config. |
| `docker_manager.py` | The heart: launch/stop/remove workspaces, build env/volumes/labels/hardening/limits, Tailscale sidecars, egress guard, image pull/remove, stats, status sync, runtime-limit enforcement. |
| `models.py` | SQLAlchemy models: `User`, `WorkspaceImage`, `Workspace`, `UserTailscale`, `AppSetting`, `AuditLog`. |
| `migrations.py` | Ordered, idempotent SQL migrations (+ a few Python data migrations) run at startup. ~20 migrations. |
| `catalog.py` | LinuxServer.io API client: fetch image catalog, build specs, fetch project logos. |
| `oidc.py` | OIDC discovery + token verification (JWKS), Authentik group → admin mapping. |
| `security.py` | Password hashing (bcrypt), JWT mint/verify, secret encryption (for Tailscale keys), username validation. |
| `net.py` | Real-client-IP extraction from forwarded headers (for rate limiting / audit). |
| `proot.py` | Lists the LinuxServer proot-apps catalog (GitHub contents API). |
| `deps.py` | FastAPI dependencies: `CurrentUser`, `AdminUser`, `DbSession`. |

**Routers** (all under `/api`):

| Prefix | Purpose |
|---|---|
| `/api/auth` | Local login/setup, logout, refresh, change-password, `/me`, OIDC login/callback, and `/forward` (Traefik ForwardAuth), stream-auth token minting. |
| `/api/workspaces` | CRUD + start/stop, live `/stats` (CPU/mem + Tailscale IP), `/stream-auth`. |
| `/api/images` | Catalog list, admin create/update/delete, `/sync`, pull + pull-status, image-only vs entry+image delete. |
| `/api/admin` | Users CRUD, app settings, env summary, audit log. Admin-gated. |
| `/api/users` | Per-user Tailscale config. |
| `/api/files` | Per-user file browser (list/upload/download/delete) confined to the user's storage. |
| `/api` (proot) | proot-apps catalog. |

### Frontend (`frontend/src/`)

Vue 3 + TypeScript + Vite + Pinia, `lucide-vue-next` icons, installable PWA
(`vite-plugin-pwa`). Built to `/app/static` and served by the backend.

- **Views** — `DashboardView` (workspace grid, split active/offline with live
  stats), `WorkspaceView` (the stream page: iframe + CRT overlay + top-bar with
  quick-switch, fullscreen, CRT, HALT), `FilesView`, `PreferencesView`,
  `SetupView`, `LoginView`, and the admin views (`AdminUsers/Images/Sessions/
  Audit/Settings`).
- **Stores** — `auth` (user + config + token), `workspaces` (items, live stats
  polling, lifecycle actions), `ui` (toasts, CRT toggle).
- **API layer** (`api/`) — thin typed `fetch` wrappers with auto token-refresh.

### Workspace images

`lscr.io/linuxserver/*` webtop & Selkies/KasmVNC images. They serve a desktop or
single-app browser on an internal port (default **3000**) and persist user data
in **`/config`**. Cove never bakes these; they're pulled on demand.

---

## 3. Authentication & stream authorization

Cove separates the **control plane** (the SPA + API, authenticated by a session)
from the **data plane** (workspace streams, authenticated separately) so a hostile
workspace can never obtain the powerful session/admin cookie.

### Control-plane auth

- **Local**: bcrypt password → short-lived access JWT (30 min) + refresh cookie
  (7 days). `tokens_valid_from` invalidates old tokens on password change.
- **OIDC**: Authorization-code flow against an OIDC issuer (e.g. Authentik).
  Tokens verified against the issuer's JWKS; a configured group claim grants
  admin. `COVE_OIDC_ONLY` disables local login entirely (gated on a working OIDC
  config so a typo can't lock everyone out).
- Cookies are host-only; `Secure` in production. Login is rate-limited by real
  client IP (Traefik middleware + app-side).

### Data-plane auth (workspace streams)

Traefik gates every workspace route through **ForwardAuth → `/api/auth/forward`**.
Two routing modes:

- **Subpath** (default): streams at `/workspace/<public_id>/` on the SPA origin.
  ForwardAuth checks the session cookie.
- **Subdomain** (`COVE_WORKSPACE_DOMAIN` set): each stream gets its **own origin**
  `<public_id>.<domain>`. The SPA mints a short-lived **stream token** via
  `/api/workspaces/{id}/stream-auth`; it's exchanged for a host-only `cove_stream`
  cookie scoped to that one workspace origin. The session cookie is never sent
  cross-origin, so a malicious workspace can only ever hold a credential for the
  workspace the user already owns. CSP `frame-ancestors` restricts who may iframe
  the stream.

When a stream's container is unreachable (still booting), Traefik's errors
middleware serves `/__cove_error/{status}` — a self-contained auto-retrying page
rendered inside the iframe.

---

## 4. Workspace lifecycle

```
launch ──▶ creating ──▶ running ──▶ (halt) ──▶ stopped ──▶ (start) ──▶ creating ...
                │                                                   
                └──▶ error (boot failed)        purge ──▶ container + network removed
```

**Launch** (`docker_manager.launch_workspace`, run as a background task):

1. Ensure the per-workspace network `cove-ws-net-<id>` exists (IPv6 off).
2. **Pull** the image (always, so workspaces stay current).
3. Build the container spec:
   - **env** — PUID/PGID/TZ, the image's URL env for browser kiosk mode, and the
     in-container app installers below.
   - **volumes** — the persistent `/config` home bind-mount + any helper init
     scripts.
   - **labels** — Traefik router/service/middleware (subpath or subdomain host),
     ForwardAuth, errors, security headers.
   - **hardening** — `cap_drop: ALL` + a minimal add-back set; `no-new-privileges`
     unless the workspace opts into sudo (or the admin force-disables it).
   - **limits** — admin-default `nano_cpus` / `mem_limit` (0 = unlimited);
     `shm_size=1g`.
   - **DNS** — custom public resolvers when enabled (non-Tailscale only).
4. **Tailscale path**: start a `cove-ts-<id>` sidecar (its own netns, `NET_ADMIN`,
   `/dev/net/tun`, state volume) carrying the Traefik labels; the workspace
   container then joins the sidecar's netns (`network_mode=container:...`) so all
   egress flows through the tailnet (and exit node, if set).
5. **Egress guard** (all workspaces): install IPv4 OUTPUT rules in the netns.
   Docker-internal (`172.16.0.0/12`) and link-local/metadata (`169.254.0.0/16`)
   are *always* dropped (backend/proxy/peer isolation); remaining private + CGNAT
   ranges are dropped too, leaving WAN-only. Admin-configured LAN subnets are
   allowed only when both the admin master toggle and the workspace's own
   `lan_access` opt-in are set. For Tailscale workspaces the guard is applied to
   the **sidecar netns before the workspace starts** (closing the startup race),
   and `tailscale0` egress is allowed first — so tailnet peers, subnet routes,
   and exit nodes keep working while the raw bridge stays firewalled.
6. Wait for readiness (Docker-API only), then flip status to `running`.

**In-container app installation** (LinuxServer `custom-cont-init.d` + Docker Mods,
wired by `docker_manager` and `scripts/`):

- **Distro packages** → `universal-package-install` mod (`INSTALL_PACKAGES`).
- **proot-apps** → `PROOT_APPS` + `scripts/install-proot-apps.sh`.
- **AppImages** → `COVE_APPIMAGES` + `scripts/install-appimages.sh`: downloads
  (curl), extracts (`--appimage-extract`, no FUSE needed in these hardened
  containers), and writes a `.desktop` launcher with `APPDIR` set and
  `--no-sandbox` for Electron apps.

**Stop / start / purge**: halting removes the container (and any Tailscale
sidecar + network); starting re-pulls and recreates; purge optionally deletes the
persistent home. `/config` survives a halt/start cycle.

**Background status monitor** (`main._status_monitor`, every 10s): reconciles DB
status with actual container state and enforces the max-runtime limit (auto-stop).

---

## 5. Data model (SQLite)

| Table | Notable columns |
|---|---|
| `user` | `username`, `password_hash` (nullable for SSO), `auth_provider` (`local`/`oidc`), `oidc_sub`, `is_admin`, `tokens_valid_from`. |
| `workspace_image` | `name`, `docker_image`, `image_type` (`desktop`/`link`), `internal_port`, `url_env`, `logo_url`, `enabled`. |
| `workspace` | `public_id` (UUID, used in routes), `user_id`, `status`, `image_id`, `container_id/name`, kiosk flags, `use_tailscale` + `ts_*`, `custom_dns`/`dns_servers`, `install_packages`/`proot_apps`/`appimages`, `allow_sudo`, `volume_name`. |
| `user_tailscale` | Per-user `auth_key` (encrypted at rest), `login_server`, enabled. |
| `app_setting` | Key/value runtime settings (see `settings_store`). |
| `audit_log` | `ts`, `user_id`/`username`, `action`, `detail`, `ip`. |

Schema is created and evolved by ordered migrations at startup (no Alembic). With
`COVE_DB_ENCRYPTION_KEY` set, the DB is encrypted with SQLCipher.

---

## 6. Security model (summary)

- **Least-privilege Docker access** via split read-only/write socket proxies; the
  raw socket is never exposed to Traefik or workspaces.
- **Per-workspace network isolation**; workspaces can't reach cove, the proxies,
  or each other. **WAN-only egress** by default (LAN ranges blocked).
- **Container hardening**: dropped capabilities, `no-new-privileges` (unless sudo
  opted in), configurable CPU/memory caps.
- **Two-tier auth**: session cookies for the control plane; separate per-workspace
  stream tokens for the data plane (subdomain mode), so a workspace can't capture
  the session/admin cookie.
- **Verified OIDC** (JWKS), short-lived JWTs with refresh + invalidation, IP-aware
  rate limiting, audit logging, optional at-rest DB encryption, secrets (Tailscale
  keys) encrypted in the DB.
- Self-service password change and admin password-set are **refused for SSO
  accounts**; local user creation is disabled in OIDC-only mode.

---

## 7. Build, test, deploy

- **Image** (`Dockerfile`): stage 1 builds the SPA (Node 22); stage 2 is the
  Python runtime that copies the backend, the built SPA into `./static`, and the
  helper `scripts/`. Runs `uvicorn` on `:8080`.
- **Compose layering**: `docker-compose.yml` (base, HTTP) +
  `docker-compose.prod.yml` (TLS-ALPN, HTTPS redirect, HSTS, secure cookies) +
  `docker-compose.dns.yml` (ACME DNS-01 / wildcard certs for subdomain mode).
- **CI** (`.gitlab-ci.yml`): lint (ruff + vue-tsc), test (pytest + vitest),
  frontend build, and a Docker image build.
- **Tests**: backend `pytest` under `backend/server/tests/`; frontend `vitest`
  under `frontend/src/**/__tests__/`.

```bash
# backend (from backend/, in a venv)
ruff check server && pytest -q
# frontend (from frontend/)
npx vue-tsc --noEmit && npm test && npm run build
```
