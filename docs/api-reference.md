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
| GET | `/api/workspaces/gpu-policy?zone_id=` | auth | Whether GPU acceleration is on for that zone (default `0`): the master toggle, or a remote zone's override. |
| GET | `/api/workspaces/docker-policy?zone_id=` | auth | Whether Docker-in-Docker is available on that zone (default `0`): the master toggle, plus the zone's own opt-in on a remote zone. |
| GET | `/api/workspaces/runtime-policy` | auth | Admin max runtime (`{"max_hours": n}`, 0 = unlimited), so cards can count down to the auto-stop. |
| GET | `/api/workspaces/{id}` | owner/admin | Get one workspace. |
| PATCH | `/api/workspaces/{id}` | owner/admin | Update workspace settings. |
| POST | `/api/workspaces/{id}/start` | owner/admin | Start/recover a stopped workspace. |
| POST | `/api/workspaces/{id}/stop` | owner/admin | Stop (and remove the container). |
| POST | `/api/workspaces/{id}/extend` | owner/admin | Restart the max-runtime clock of a running workspace from now (sets `runtime_extended_at`). `400` unless running. |
| POST | `/api/workspaces/{id}/clone` | owner/admin | Clone a stopped workspace. |
| POST | `/api/workspaces/{id}/migrate` | owner/admin | Move a stopped workspace to another zone (`{"target_zone_id": n}`); copies `/config` across, then removes the source copy. `409` unless stopped. See [Zones](zones.md). |
| DELETE | `/api/workspaces/{id}` | owner/admin | Delete (optional `?purge_storage=true`). |
| POST | `/api/workspaces/{id}/stream-auth` | owner/admin | Mint the iframe stream URL/token. |
| GET | `/api/workspaces/{id}/stream-ready` | owner/admin | Whether Traefik already has a route for the stream (the SPA polls this before loading the iframe). |
| POST | `/api/workspaces/{id}/stream-lease` | owner/admin | `{"viewer_id"}`: this page's stream loaded, so it holds the workspace now. |
| GET | `/api/workspaces/{id}/stream-lease?viewer_id=` | owner/admin | Does this page still hold the stream? `{holder, label, since}`: when `holder` is false, `label` names where it moved ("Desk (thin client)", "Firefox on Linux"). A missing or expired lease is re-claimed by the asker. |
| DELETE | `/api/workspaces/{id}/stream-lease?viewer_id=` | owner/admin | The holding page closed. |
| GET | `/api/workspaces/{id}/logs` | owner/admin | Container logs (desktop / tailscale / gluetun). |
| GET | `/api/workspaces/{id}/tailscale-status` | owner/admin | `tailscale status` from the sidecar. |
| GET | `/api/workspaces/{id}/info` | owner/admin | System info for the stream page in one call: uptime / auto-stop, container (IDs, restarts, real CPU/memory/PID/shm limits, hardening), live resources (CPU, memory, processes, network and block I/O totals), network route with profile and sidecar health, storage kind, features. Fields are `null` when not running or not reported. |
| GET | `/api/workspaces/{id}/disk-usage` | owner/admin | Size of the workspace home: `{bytes, files, partial}` (`partial` when counting hit its time budget). `404` when there is no home Cove can measure on this server (ephemeral, or on a remote zone). |
| POST | `/api/workspaces/{id}/public-ip` | owner/admin | Look up the workspace's public IP from inside it (via ipinfo.io), so it shows the VPN/Tailscale exit. `409` unless running. |
| GET | `/api/workspaces/{id}/fs?path=` | owner/admin | List a folder of the running workspace's home (`/config`); paths are home-relative and can't leave it. `409` unless running. |
| GET | `/api/workspaces/{id}/fs/download?path=` | owner/admin | Download one file from the workspace (streamed; `400` for a folder). Audited. |
| POST | `/api/workspaces/{id}/fs/upload` | owner/admin | Multipart `path` (folder, created if missing) + `file` → `201 {path, name, size}`. Written as the workspace user; an existing name gets ` (2)`. `413` over the upload limit. Audited. |
| POST | `/api/workspaces/{id}/fs/mkdir` | owner/admin | `{"path": "a/b"}` → `201`. |
| GET | `/api/workspaces/{id}/manifest.webmanifest` | owner/admin | Per-workspace PWA manifest. |
| GET | `/api/workspaces/{id}/preview.jpg` | owner/admin | Stored still frame of the workspace screen (`404` when there is none). `private` caching + ETag. |
| POST | `/api/workspaces/{id}/preview/refresh` | owner/admin | Re-capture the preview from an already-running stream (passive only; `409` unless the workspace is running). |
| GET | `/api/workspaces/{id}/favicon.png` | owner/admin | Favicon of the site a browser workspace opens (`404` when there is none). |
| GET | `/api/workspaces/{id}/proot-apps` | owner/admin | Installed proot-apps with `update_available` per app (`?check=false` skips the registry). `409` unless a running desktop. |
| POST | `/api/workspaces/{id}/proot-apps/tasks` | owner/admin | Queue a background task: `{"op": "install"\|"update"\|"remove", "apps": [...]}` → `202`. Install/remove also update the saved `proot_apps`. `429` when three are already active. |
| GET | `/api/workspaces/{id}/proot-apps/tasks` | owner/admin | The workspace's proot-apps tasks (`queued`/`running`/`done`/`failed`/`interrupted`). |
| GET | `/api/workspaces/{id}/proot-apps/tasks/{task_id}/log` | owner/admin | Tail (64 KB) of one task's output. |
| POST | `/api/workspaces/{id}/proot-apps/tasks/clear` | owner/admin | Forget finished tasks. |
| GET | `/api/workspaces/{id}/appimages` | owner/admin | Installed AppImages: slug, name, source URL (null if unknown), size, install time. |
| POST | `/api/workspaces/{id}/appimages/tasks` | owner/admin | Queue an AppImage task → `202`: `{"op":"install","urls":[…]}`, `{"op":"update","slug":…,"url":…}` (the URL replaces that app), or `{"op":"remove","slugs":[…]}`. Updates the saved `appimages` list. URLs are downloaded **by the workspace**, never by Cove. |

The `proot-apps` endpoints run a short command inside the workspace, so each workspace (2) and each caller (4) gets only a few at a time; beyond that they answer `429`.

## Images — `/api/images`

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/images` | auth | List enabled catalog images. |
| GET | `/api/images/pull-status` | admin | Per-image local availability. |
| GET | `/api/images/availability` | auth | Same, for enabled images, so the launch wizard can warn that a pick still has to be downloaded. |
| GET | `/api/images/details` | admin | Per image on a zone: status, size, last pulled, live pull progress, and how many workspaces use it. |
| POST | `/api/images/{id}/check-update` | admin | Compare the local digest with what the registry serves at the tag now (anonymous lookup; `up_to_date: null` when it can't tell). |
| POST | `/api/images/{id}/pull` | admin | Background pull / re-pull. |
| POST | `/api/images/sync` | admin | Sync the catalog from LinuxServer (optional `?reset=true` also force-reapplies curated type/port/`url_env` to existing entries). |
| POST | `/api/images` | admin | Add a custom image. |
| PATCH | `/api/images/{id}` | admin | Edit (name, `docker_image`, `image_type`, port, `url_env`, logo) / enable / disable. |
| DELETE | `/api/images/{id}/image` | admin | Delete the local image, keep the entry. |
| DELETE | `/api/images/{id}` | admin | Delete the catalog entry (optional `?remove_image=true`). |

## Users (self-service) — `/api/users`

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET / POST | `/api/users/me/tailscale-profiles` | auth | List / add your Tailscale profiles (auth key never returned). |
| PUT / DELETE | `/api/users/me/tailscale-profiles/{id}` | auth | Update / delete one (409 while a workspace uses it). |
| GET / POST | `/api/users/me/vpn-profiles` | auth | List / add your Gluetun VPN profiles (secrets masked; includes `problem`, `used_by`, `active_in`). |
| PUT / DELETE | `/api/users/me/vpn-profiles/{id}` | auth | Update / delete one (409 while a workspace uses it). |
| GET | `/api/users/vpn-providers` | auth | Gluetun built-in providers usable from a key / login, with their protocols. |
| GET | `/api/users/me/ssh` | auth | Your SSH public key + metadata (never the private key). |
| PUT | `/api/users/me/ssh` | auth | Upload (or clear) an SSH private key. |
| POST | `/api/users/me/ssh/generate` | auth | Generate a fresh Ed25519 keypair. |
| DELETE | `/api/users/me/ssh` | auth | Clear your SSH key. |

## Admin — `/api/admin` (all admin)

| Method | Path | Purpose |
|---|---|---|
| GET / POST | `/api/admin/users` | List / create users. |
| PATCH / DELETE | `/api/admin/users/{id}` | Update / delete a user. `zones_restricted` + `allowed_zone_ids` set the user's zone limit (either may be sent alone; unknown zone ids are a `400`). |
| POST | `/api/admin/users/{id}/revoke-sessions` | Sign the user out everywhere (every token issued before now stops working). |
| GET | `/api/admin/sessions` | List every user's running/starting workspaces. |
| DELETE | `/api/admin/sessions/{id}` | Stop one of them (the workspace is kept; it used to be deleted). `400` unless running/starting. |
| GET | `/api/admin/audit` | Audit entries, newest first. Filters: `user`, `action` (exact, or a prefix ending in `.`), `since`/`until` (ISO), `q` (detail substring), `limit` (≤1000, default 200), `offset`. Workspace entries carry `workspace_id`/`workspace_name` while the workspace exists. |
| GET | `/api/admin/audit/facets` | Distinct users and actions, for the filter dropdowns. |
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
| GET | `/api/admin/zones/{id}/health` | admin | Live check of the zone's Docker daemon: `ok`, round-trip ms, engine version, running containers, `agent_dind` (whether the agent's host allows Docker-in-Docker) and `agent_current` (whether the agent runs the control plane's own build). |
| PATCH | `/api/admin/zones/{id}` | admin | Update name / endpoint host / port, and the zone's settings: `gpu_accel`, `gpu_render_node`, `gpu_render_gid` (`null` = inherit the global setting) and `docker_enabled` (`400` for the local zone `0`, whose settings are the global ones). |
| DELETE | `/api/admin/zones/{id}` | admin | Delete a zone (`409` while workspaces are still pinned to it; `400` for zone `0`). |
| POST | `/api/admin/zones/{id}/enroll-token` | admin | Mint a single-use enrollment token + install one-liner. The plaintext token is shown once; only its sha256 is stored. Requires the endpoint host to be set. |
| POST | `/api/admin/zones/{id}/rotate-client-cert` | admin | Re-issue the control plane's mTLS **client** cert for the zone (`409` if it has no mTLS material). The agent's server cert is rotated by re-enrolling instead. |
| POST | `/api/admin/zones/{id}/update-agent` | admin | Update the zone's agent in place to the control plane's current agent image (`202`, runs in the background; `502` if the agent is unreachable, `409` if it predates the updater sidecar or the control plane can't find its own image). |
| GET | `/api/zones` | auth | Minimal `{id, name}` list of the enrolled zones the caller may use (see per-user zone limits under Administration → User management), for the launch and migrate pickers — never endpoints or cert material. |
| GET | `/api/zones/agent-image?token=` | enrollment token | Stream the agent image as a `docker save` tar so a fresh host can `docker load` it with no registry. Validates the token without consuming it. |
| POST | `/api/zones/enroll?token=` | enrollment token (single-use) | Sign the agent's CSR; returns the CA cert, signed server cert, stream signing key, workspace domain, and expected client CN. `409` on replay. |
| GET | `/install.sh?token=` | enrollment token | The generated installer script (served at the root, not under `/api`). |

## Thin clients — `/api/admin/thin-clients`, `/api/thin-client`

See [Thin clients](thin-clients.md) for the device side and the session model.

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/admin/thin-clients` | admin | List devices with their policy, last reported state, session and online status. |
| POST | `/api/admin/thin-clients` | admin | Register a device (`{"name", "location"}`); it stays `pending` until enrolled. |
| GET | `/api/admin/thin-clients/{id}` | admin | Get one device. |
| PATCH | `/api/admin/thin-clients/{id}` | admin | Update name, location and/or `policy` (validated; bumps `policy_version`). |
| DELETE | `/api/admin/thin-clients/{id}` | admin | Delete a device: its token stops working and its sessions end. |
| POST | `/api/admin/thin-clients/{id}/enroll-token` | admin | Mint a single-use enrolment code (`{"ttl_hours": 1..168}`) and install one-liner. Only its sha256 is stored. Using it on an enrolled device replaces that device's token. |
| GET/POST | `/api/admin/thin-clients/{id}/commands` | admin | List the last 50 commands, or queue one (`screen_on`, `screen_off`, `restart_browser`, `identify`, `screenshot`, `apt_upgrade`, `reboot`, `poweroff`, `logs`, `set_volume` with `{"level": 0..150}`, `set_mute` with `{"muted": bool}`). `409` if not enrolled, `400` for an unknown kind. |
| POST | `/api/admin/thin-clients/{id}/signout` | admin | Revoke the device's sessions and reset its browser. |
| POST | `/api/admin/thin-clients/{id}/keep-awake` | admin | Treat the device as active for `{"minutes"}` (0 clears it). |
| GET | `/api/admin/thin-clients/{id}/screenshot.png` | admin | The latest screenshot (`404` if none). |
| GET | `/api/admin/thin-clients/workspace-options` | admin | Every workspace with its owner, for the pinned-workspace picker. |
| GET | `/thin-client/install.sh?code=` | enrolment code | The install script. Validates the code without consuming it. Rate-limited. |
| POST | `/api/thin-client/enroll?code=` | enrolment code (single-use) | Returns the device token. `409` on replay. Rate-limited. |
| POST | `/api/thin-client/checkin` | device token | Report state, receive policy, allowed hosts, commands and the current agent's version and sha256. With `wait` (≤ 25 s) and nothing to do, it is held open as a long-poll. |
| POST | `/api/thin-client/commands/{id}/result` | device token | Report a command's outcome (`404` for another device's command). |
| POST | `/api/thin-client/screenshot` | device token | Upload a PNG (≤ 8 MiB). |
| POST | `/api/thin-client/ticket` | device token | A one-time (120 s) browser-binding URL. |
| POST | `/api/thin-client/signed-out` | device token | The device signed itself out (idle): revoke its sessions. |
| GET | `/api/thin-client/agent` | device token | The agent file, with `X-Agent-Sha256` and `X-Agent-Version`. |
| GET | `/thin-client/bind?ticket=` | one-time ticket | Sets the httpOnly `cove_tc` cookie that binds this browser to the device, then redirects to `/app`. `403` page (fails closed) for a bad or reused ticket. |
| GET | `/api/thin-client/self` | `cove_tc` cookie | Which device this browser is bound to, and its auto-open settings (`404` on a normal browser). |

## Integrations — `/api/admin/integration-tokens`, `/api/integration/v1`

See [Home Assistant](home-assistant.md). The `/api/integration/v1` endpoints take
an integration token (`Authorization: Bearer cit_…`) and nothing else; the token
is accepted nowhere else.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/admin/integration-tokens` | admin | List tokens (name, scope, prefix, created, last used and from where). Never the token. |
| POST | `/api/admin/integration-tokens` | admin | `{"name", "scope": "read"\|"control"}` → `201` with the plaintext `token`, shown this once. Only its sha256 is stored. |
| DELETE | `/api/admin/integration-tokens/{id}` | admin | Revoke a token. |
| GET | `/api/admin/integration-tokens/home-assistant.zip` | admin | The Home Assistant custom component (`custom_components/cove/…`). `404` when the build doesn't include it. |
| GET | `/api/integration/v1/status` | read | Users, workspaces (by state), zones (online, counts, summed workspace CPU/memory) and enrolled thin clients (online, session, workspace on screen, screen, volume, updates…), plus the token's name and scope. Tokens are revoked when their creator is deleted or loses admin. |
| GET | `/api/integration/v1/thin-clients/{public_id}/screenshot.png` | control | The latest screenshot (`404` if none). |
| POST | `/api/integration/v1/thin-clients/{public_id}/commands` | control | Queue `screen_on`, `screen_off`, `restart_browser`, `identify`, `screenshot`, `reboot`, `poweroff`, `set_volume` (`{"level": 0..150}`) or `set_mute` (`{"muted": bool}`) → `202`. Anything else is a `400`. |
| POST | `/api/integration/v1/thin-clients/{public_id}/signout` | control | Revoke the device's sessions and reset its browser. |
| POST | `/api/integration/v1/thin-clients/{public_id}/keep-awake` | control | `{"minutes"}` (0 clears it). |

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
| GET | `/api/extension` | auth | Whether this build ships the "Open in Cove" browser extension, and its version/size. |
| GET | `/api/extension/download` | auth | The extension as a zip (`ETag`, `304` on revalidation). `404` when the build doesn't include it. |
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
| `/app/admin/thin-clients` | Thin clients | admin |
| `/app/admin/storage` | Storage | admin |
| `/app/admin/integrations` | Integrations | admin |
| `/app/admin/audit` | Audit | admin |
| `/app/admin/settings` | Settings | admin |
