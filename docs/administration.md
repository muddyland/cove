# Administration

Admin users get an **Admin** section in the top navigation with five tabs:
**Users**, **Sessions**, **Images**, **Audit**, and **Settings**. All admin
endpoints require an admin account; non-admins receive `403`.

## Settings

**Admin → Settings** edits database-backed runtime settings that apply to **newly
started** workspaces (no restart needed). Existing running workspaces keep the
limits they started with.

| Setting | Default | Effect |
|---|---|---|
| **Default CPU limit (cores)** | `0` (unlimited) | Caps each new workspace container's CPU; fractions allowed. |
| **Default memory limit (MB)** | `0` (unlimited) | Caps each new workspace container's RAM. |
| **Max runtime (hours)** | `24` (`0` = unlimited) | Running workspaces older than this are auto-stopped. |
| **Force-disable sudo** | off | Applies `no-new-privileges` to **all** workspaces, overriding the per-launch "Allow sudo". |
| **GPU acceleration** (master toggle) | off | Allows workspaces to use host-GPU hardware video encode (each workspace must still opt in, and needs Wayland streaming). See [Workspaces → GPU acceleration](workspaces.md#gpu-acceleration). |
| **GPU render node** | `/dev/dri/renderD128` | DRI device bind-mounted for GPU workspaces. Change only on multi-GPU hosts. |
| **GPU render group GID** | `992` | Group added so the workspace can open the render node. **Fallback only** — Cove auto-detects the node's real group on each host at launch; set this if detection can't run. |
| **Docker-in-Docker** (master toggle) | off | Allows workspaces to run a privileged nested Docker daemon (each workspace must still opt in; local zone only). |
| **LAN access** (master toggle) | off | Allows workspaces to reach the LAN directly (each workspace must still opt in). |
| **Allowed LAN subnets** | _(empty)_ | IPv4 CIDRs reachable when LAN access is on. Invalid entries are dropped; bare IPs become `/32`. |
| **Tailscale sidecar image** | `tailscale/tailscale:latest` | Image used for the Tailscale sidecar (pin a tag/digest). |
| **Gluetun sidecar image** | `qmcgaw/gluetun:latest` | Image used for the Gluetun VPN sidecar. |

> The Docker-internal range (`172.16.0.0/12`) and cloud-metadata range
> (`169.254.0.0/16`) are **always** blocked regardless of the LAN subnet list.

The page also shows a **read-only summary** of the env-configured settings
(domain, cookie-secure, token lifetimes, upload limit, timezone, OIDC status, DB
encryption, …) so you can confirm what the running process loaded. Secrets are
reported only as present/absent — never shown.

## User management

**Admin → Users** lists every account.

- **Create** — username + password (+ optional admin). Username rules: 1–64 chars of `[a-zA-Z0-9._-]`, not `.`/`..`; password ≥ 8 chars. Disabled in [OIDC-only mode](authentication.md#oidc-only-mode).
- **Edit** — rename, toggle admin, or reset the password. Notes: you **cannot demote the last admin**; passwords can only be set on **local** accounts (not SSO); a password reset invalidates that user's existing sessions.
- **Delete** — removes the account and background-stops all of that user's running workspaces. You can't delete your own account.

## Image catalog

**Admin → Images** manages the catalog of launchable images. On first run the
catalog auto-seeds from the LinuxServer.io API; thereafter:

- **Sync LinuxServer** — re-fetch the curated catalog from `api.linuxserver.io`, adding new entries and backfilling logos/descriptions while preserving your edits (name, enabled state, port). Returns counts of added/updated.
- **Pull / Re-pull** — pull an image to the host in the background. Each image shows a live status (**present / absent / pulling**); the view polls while anything is pulling.
- **Add custom image** — add your own entry: name, `docker_image`, type (`desktop` or `link`), internal port (default 3000), and an optional description.
- **Edit / Enable / Disable** — rename, retarget, or hide an entry. Only **enabled** images appear in the launch UI.
- **Delete** — either remove just the **local image** (keep the catalog entry; it becomes "absent") or remove the **catalog entry** (optionally also deleting the local image). An image in use by a workspace can't be removed (`409`).

> Newly started workspaces always pull the latest image first, so a re-pull is
> mostly useful to pre-warm an image or to reclaim space by removing unused ones.

## Live sessions

**Admin → Sessions** shows **all** workspaces across **all** users that are
`running`, `creating`, or `stopping` — name, status, image, and start time. Use
**Kill** to stop and remove any session. (The regular dashboard only ever shows a
user their own workspaces, even for admins; this tab is the cross-user view.)

## Audit log

**Admin → Audit** shows the most recent **200** audited actions — timestamp,
user, action, detail, and client IP. Recorded actions include:

- **Auth:** `setup`, `login.success`, `login.fail`, `login.oidc`, `logout`, `stream.deny`.
- **Workspaces:** `workspace.launch`, `workspace.clone`, `workspace.update`, `workspace.stop`, `workspace.start`, `workspace.delete`.
- **Files:** `files.upload`, `files.delete`.
- **User secrets:** `user.tailscale.update`, `user.gluetun.update`, `user.ssh.upload`, `user.ssh.generate`, `user.ssh.clear`.
- **Admin:** `admin.user.create`, `admin.user.update`, `admin.user.delete`, `admin.session.kill`, `admin.settings.update`.

The view shows the latest 200; there is no automatic pruning of the underlying
table.

## SPA routes

For reference, the admin views live at:

```
/app/admin/users      /app/admin/sessions    /app/admin/images
/app/admin/audit       /app/admin/settings
```

These are guarded — non-admins are redirected to `/app`. The full route list is in
[API reference → SPA routes](api-reference.md#spa-routes).
</content>
