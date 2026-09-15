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
| GET | `/api/workspaces/gpu-policy` | auth | GPU acceleration master toggle for the launch modal. |
| GET | `/api/workspaces/docker-policy` | auth | Docker-in-Docker master toggle for the launch modal. |
| GET | `/api/workspaces/{id}` | owner/admin | Get one workspace. |
| PATCH | `/api/workspaces/{id}` | owner/admin | Update workspace settings. |
| POST | `/api/workspaces/{id}/start` | owner/admin | Start/recover a stopped workspace. |
| POST | `/api/workspaces/{id}/stop` | owner/admin | Stop (and remove the container). |
| POST | `/api/workspaces/{id}/clone` | owner/admin | Clone a stopped workspace. |
| POST | `/api/workspaces/{id}/migrate` | owner/admin | Move a stopped workspace to another zone (`{"target_zone_id": n}`); copies `/config` across, then removes the source copy. `409` unless stopped. See [Zones](zones.md). |
| DELETE | `/api/workspaces/{id}` | owner/admin | Delete (optional `?purge_storage=true`). |
| POST | `/api/workspaces/{id}/stream-auth` | owner/admin | Mint the iframe stream URL/token. |
| GET | `/api/workspaces/{id}/stream-ready` | owner/admin | Whether Traefik already has a route for the stream (the SPA polls this before loading the iframe). |
| GET | `/api/workspaces/{id}/logs` | owner/admin | Container logs (desktop / tailscale / gluetun). |
| GET | `/api/workspaces/{id}/tailscale-status` | owner/admin | `tailscale status` from the sidecar. |
| GET | `/api/workspaces/{id}/manifest.webmanifest` | owner/admin | Per-workspace PWA manifest. |
| GET | `/api/workspaces/{id}/preview.jpg` | owner/admin | Stored still frame of the workspace screen (`404` when there is none). `private` caching + ETag. |
| POST | `/api/workspaces/{id}/preview/refresh` | owner/admin | Re-capture the preview from an already-running stream (passive only; `409` unless the workspace is running). |
| GET | `/api/workspaces/{id}/favicon.png` | owner/admin | Favicon of the site a browser workspace opens (`404` when there is none). |
| GET | `/api/workspaces/{id}/proot-apps` | owner/admin | Installed proot-apps with `update_available` per app (`?check=false` skips the registry). `409` unless a running desktop. |
| POST | `/api/workspaces/{id}/proot-apps/tasks` | owner/admin | Queue a background task: `{"op": "install"\|"update"\|"remove", "apps": [...]}` → `202`. Install/remove also update the saved `proot_apps`. `429` when three are already active. |
| GET | `/api/workspaces/{id}/proot-apps/tasks` | owner/admin | The workspace's proot-apps tasks (`queued`/`running`/`done`/`failed`/`interrupted`). |
| GET | `/api/workspaces/{id}/proot-apps/tasks/{task_id}/log` | owner/admin | Tail (64 KB) of one task's output. |
| POST | `/api/workspaces/{id}/proot-apps/tasks/clear` | owner/admin | Forget finished tasks. |

The `proot-apps` endpoints run a short command inside the workspace, so each workspace (2) and each caller (4) gets only a few at a time; beyond that they answer `429`.

## Images — `/api/images`

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/images` | auth | List enabled catalog images. |
| GET | `/api/images/pull-status` | admin | Per-image local availability. |
| POST | `/api/images/{id}/pull` | admin | Background pull / re-pull. |
| POST | `/api/images/sync` | admin | Sync the catalog from LinuxServer (optional `?reset=true` also force-reapplies curated type/port/`url_env` to existing entries). |
| POST | `/api/images` | admin | Add a custom image. |
| PATCH | `/api/images/{id}` | admin | Edit (name, `docker_image`, `image_type`, port, `url_env`, logo) / enable / disable. |
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
| GET | `/api/admin/storage` | Per-zone disk usage: host free space plus a Docker breakdown (images, containers, volumes, build cache) with reclaimable amounts. |
| POST | `/api/admin/storage/prune` | Reclaim Docker disk on a zone (`{"zone_id": n, "deep": bool}`). Dangling images + build cache by default; `deep` also removes all unused images and stopped containers. Volumes are never touched. `502` if the daemon call fails. |
| GET / PUT | `/api/admin/settings` | Get / update runtime settings. |

## Zones — `/api/admin/zones`, `/api/zones`

See [Zones](zones.md) for the enrollment flow and the agent stack these drive.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/admin/zones` | admin | List all zones (each with its workspace count). |
| POST | `/api/admin/zones` | admin | Register a zone. With an endpoint host it is immediately `enrolled`; without one it waits as `pending`. |
| GET | `/api/admin/zones/{id}` | admin | Get one zone. |
| PATCH | `/api/admin/zones/{id}` | admin | Update name / endpoint host / port (`400` for the local zone `0`). |
| DELETE | `/api/admin/zones/{id}` | admin | Delete a zone (`409` while workspaces are still pinned to it; `400` for zone `0`). |
| POST | `/api/admin/zones/{id}/enroll-token` | admin | Mint a single-use enrollment token + install one-liner. The plaintext token is shown once; only its sha256 is stored. Requires the endpoint host to be set. |
| POST | `/api/admin/zones/{id}/rotate-client-cert` | admin | Re-issue the control plane's mTLS **client** cert for the zone (`409` if it has no mTLS material). The agent's server cert is rotated by re-enrolling instead. |
| POST | `/api/admin/zones/{id}/update-agent` | admin | Update the zone's agent in place to the control plane's current agent image (`202`, runs in the background; `502` if the agent is unreachable, `409` if it predates the updater sidecar). |
| GET | `/api/zones` | auth | Minimal `{id, name}` list of enrolled zones for the launch and migrate pickers — never endpoints or cert material. |
| GET | `/api/zones/agent-image?token=` | enrollment token | Stream the agent image as a `docker save` tar so a fresh host can `docker load` it with no registry. Validates the token without consuming it. |
| POST | `/api/zones/enroll?token=` | enrollment token (single-use) | Sign the agent's CSR; returns the CA cert, signed server cert, stream signing key, workspace domain, and expected client CN. `409` on replay. |
| GET | `/install.sh?token=` | enrollment token | The generated installer script (served at the root, not under `/api`). |

## Files — `/api/files` (all auth)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/files?path=` | List a directory in your storage area. |
| GET | `/api/files/download?path=` | Download a file. |
| POST | `/api/files/upload` | Upload a file (`413` if over `COVE_MAX_UPLOAD_MB`). |
| DELETE | `/api/files?path=` | Delete a file or directory (hard delete). |
| GET | `/api/files/download-archive?path=` | Download a folder (or file) as a streamed zip. |
| POST | `/api/files/copy` | Copy `{src, dst_dir}`. Never overwrites — a colliding name is suffixed. |
| POST | `/api/files/move` | Move `{src, dst_dir}`. Same collision handling as copy. |
| POST | `/api/files/trash` | Soft delete: move `{path}` into your trash (`201`). |
| GET | `/api/files/trash` | List your trash entries. |
| POST | `/api/files/trash/{id}/restore` | Restore an entry to its original path. |
| DELETE | `/api/files/trash/{id}` | Permanently purge one entry. |

Every path-based endpoint above takes an optional `zone_id` (default `0`, the
local zone). For a remote zone the call is proxied to that zone's agent over
mTLS, so a zone that isn't enrolled yet returns `409`. The two `/trash/{id}`
endpoints don't take it — they use the zone the entry was trashed on. Trash
entries expire per **trash retention (days)** and are swept hourly; see
[Administration → Settings](administration.md#settings).

## Misc

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/proot-apps` | auth | List installable proot-app names. |
| GET | `/api/proot-tasks` | auth | proot-apps tasks across your own running desktop workspaces (the navbar tasks menu). |
| GET | `/api/health` | public | Health check. |
| GET | `/api/docs` | auth | List the bundled documentation pages (`slug`, `title`, `scope`). Non-admins receive only `scope: "user"` pages. |
| GET | `/api/docs/{slug}` | auth (admin for admin-scoped pages) | One page's Markdown plus its `scope`. `403` if a non-admin requests an admin-scoped page, `404` for a malformed slug. |
| GET | `/api/internal/traefik-config` | internal (Traefik HTTP provider) | Dynamic config for workspaces on remote zones. `404` unless the request arrives with the internal Host. |

## SPA routes

The app lives under `/app` (older paths redirect to it). Documentation has no
route of its own — it opens as a modal from the **?** in the top bar, so
`/app/docs` and `/app/docs/:slug` redirect to the dashboard.

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
| `/app/admin/zones` | Zones | admin |
| `/app/admin/storage` | Storage | admin |
| `/app/admin/audit` | Audit | admin |
| `/app/admin/settings` | Settings | admin |
