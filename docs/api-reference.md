# API reference

Cove's backend is a FastAPI app under `/api`. The Vue SPA is served for all other
paths. This page lists every endpoint, the auth model, and the SPA routes.

## Auth model

- **Public** — no authentication.
- **Auth** — requires a valid session: the `cove_session` cookie **or** an `Authorization: Bearer <token>` header.
- **Admin** — requires an authenticated user with `is_admin` (else `403`).
- Owner/admin endpoints additionally require that you own the workspace (or are an admin).

**CSRF:** cookie-authenticated, mutating `/api/**` requests are rejected if they
come from a cross-origin context. Bearer-token requests bypass this (they're not
ambient credentials). See [Authentication](authentication.md) for tokens and
cookies.

## Auth — `/api/auth`

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/auth/config` | public | OIDC enabled, provider name, `needs_setup`, `oidc_only`. |
| POST | `/api/auth/setup` | public (first-run only) | Create the first admin account. |
| POST | `/api/auth/login` | public | Local login (rate-limited). |
| POST | `/api/auth/refresh` | public (refresh cookie) | Rotate session/refresh cookies. |
| GET | `/api/auth/me` | auth | Current user info. |
| POST | `/api/auth/logout` | auth | Revoke tokens + clear cookies. |
| POST | `/api/auth/change-password` | auth (local) | Change own password (rate-limited). |
| GET | `/api/auth/oidc/login` | public | Begin the OIDC redirect. |
| GET | `/api/auth/oidc/callback` | public | OIDC callback; provisions/updates the SSO user. |
| GET | `/api/auth/forward` | internal (Traefik ForwardAuth) | Authorize a workspace stream. |

## Workspaces — `/api/workspaces`

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/workspaces` | auth | List your own workspaces. |
| POST | `/api/workspaces` | auth | Create and launch a workspace. |
| GET | `/api/workspaces/stats` | auth | Live CPU/memory of your running workspaces. |
| GET | `/api/workspaces/lan-policy` | auth | LAN egress policy (toggle + subnets) for the launch modal. |
| GET | `/api/workspaces/{id}` | owner/admin | Get one workspace. |
| PATCH | `/api/workspaces/{id}` | owner/admin | Update workspace settings. |
| POST | `/api/workspaces/{id}/start` | owner/admin | Start/recover a stopped workspace. |
| POST | `/api/workspaces/{id}/stop` | owner/admin | Stop (and remove the container). |
| POST | `/api/workspaces/{id}/clone` | owner/admin | Clone a stopped workspace. |
| DELETE | `/api/workspaces/{id}` | owner/admin | Delete (optional `?purge_storage=true`). |
| POST | `/api/workspaces/{id}/stream-auth` | owner/admin | Mint the iframe stream URL/token. |
| GET | `/api/workspaces/{id}/logs` | owner/admin | Container logs (desktop / tailscale / gluetun). |
| GET | `/api/workspaces/{id}/tailscale-status` | owner/admin | `tailscale status` from the sidecar. |
| GET | `/api/workspaces/{id}/manifest.webmanifest` | owner/admin | Per-workspace PWA manifest. |

## Images — `/api/images`

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/images` | auth | List enabled catalog images. |
| GET | `/api/images/pull-status` | admin | Per-image local availability. |
| POST | `/api/images/{id}/pull` | admin | Background pull / re-pull. |
| POST | `/api/images/sync` | admin | Sync the catalog from LinuxServer. |
| POST | `/api/images` | admin | Add a custom image. |
| PATCH | `/api/images/{id}` | admin | Edit / enable / disable. |
| DELETE | `/api/images/{id}/image` | admin | Delete the local image, keep the entry. |
| DELETE | `/api/images/{id}` | admin | Delete the catalog entry (optional `?remove_image=true`). |

## Users (self-service) — `/api/users`

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET / PUT | `/api/users/me/tailscale` | auth | Get / update your Tailscale config (secrets masked). |
| GET / PUT | `/api/users/me/gluetun` | auth | Get / update your Gluetun config (secrets masked). |
| GET | `/api/users/me/ssh` | auth | Your SSH public key + metadata (never the private key). |
| PUT | `/api/users/me/ssh` | auth | Upload (or clear) an SSH private key. |
| POST | `/api/users/me/ssh/generate` | auth | Generate a fresh Ed25519 keypair. |
| DELETE | `/api/users/me/ssh` | auth | Clear your SSH key. |

## Admin — `/api/admin` (all admin)

| Method | Path | Purpose |
|---|---|---|
| GET / POST | `/api/admin/users` | List / create users. |
| PATCH / DELETE | `/api/admin/users/{id}` | Update / delete a user. |
| GET | `/api/admin/sessions` | List all live sessions (cross-user). |
| DELETE | `/api/admin/sessions/{id}` | Kill a session. |
| GET | `/api/admin/audit` | Last 200 audit entries. |
| GET | `/api/admin/env` | Read-only env summary. |
| GET / PUT | `/api/admin/settings` | Get / update runtime settings. |

## Files — `/api/files` (all auth)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/files?path=` | List a directory in your storage area. |
| GET | `/api/files/download?path=` | Download a file. |
| POST | `/api/files/upload` | Upload a file (`413` if over `COVE_MAX_UPLOAD_MB`). |
| DELETE | `/api/files?path=` | Delete a file or directory. |

## Misc

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/proot-apps` | auth | List installable proot-app names. |
| GET | `/api/health` | public | Health check. |

## SPA routes

The app lives under `/app` (older paths redirect to it).

| Path | View | Access |
|---|---|---|
| `/app/login` | Login | public |
| `/app/setup` | First-run setup | public (first run) |
| `/app` | Dashboard | auth |
| `/app/files` | File browser | auth |
| `/app/preferences` | Preferences | auth |
| `/app/workspace/:id` | Workspace stream (in-app) | auth |
| `/workspace/:id` | Workspace stream (standalone PWA entry) | auth |
| `/app/admin/users` | Users | admin |
| `/app/admin/sessions` | Sessions | admin |
| `/app/admin/images` | Images | admin |
| `/app/admin/audit` | Audit | admin |
| `/app/admin/settings` | Settings | admin |
</content>
