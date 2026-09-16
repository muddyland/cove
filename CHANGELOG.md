# Changelog

## Unreleased

- **Cove serves the browser extension.** *Open in Cove* is vendored into the image
  and offered under **Preferences → Browser extension** as a zip, with install
  steps — no store, no internet access needed. It is for Chrome-based browsers;
  the Firefox manifest entry it carried was never a build anyone ran. The
  extension now also hides start-up options the chosen browser ignores, the same
  way the launcher does.

- **Helium, Vivaldi and Opera** join the browser catalog (run **Admin → Images →
  Sync LinuxServer** to pick them up).
- **Start-up options follow the browser.** Kiosk mode, dark mode and the
  "allow the menu" variant are only offered where the chosen browser honours
  them, and unsupported flags are no longer passed. Verified per image: Firefox
  takes its own `--kiosk` but has no full-screen or dark switch; Vivaldi honours
  none of them. Firefox previously received Chromium flags that did nothing.
- **Dark mode stands on its own again** in the launcher, rather than only under
  kiosk, and starts on when the browser you're launching from is in dark mode.
  Previously the form only sent it when kiosk was on, so "dark without kiosk"
  silently did nothing; the flags themselves always worked.
- **Browser workspaces no longer offer sudo or SSH-key injection.** Neither means
  anything without a terminal, and sudo would drop the container's
  no-new-privileges for nothing. They're hidden in the launcher and the edit
  form, refused on create/edit, and ignored at launch for rows that already carry
  them. The extension drops its sudo toggle for the same reason.
- **One launcher.** *Deploy Node* and *Open Website* are now a single **Launch**
  flow that asks what you want — Desktop, Browser or App — and then shows only
  the images of that kind. Browser launches keep the URL entry and options the
  old website flow had, including remote-zone launches always being ephemeral.

- **Manage AppImages from the Apps dialog.** A workspace's installed AppImages
  are listed with their size and source URL, and can be installed (paste a URL),
  updated (paste the URL to install over one — the new copy is swapped in only
  after it extracts, so a failed update keeps the working app) and removed. The
  saved list follows along, so rebuilds and migrations reinstall what you have.
  There is deliberately no update check: an AppImage URL carries no version to
  compare. Cove never fetches these URLs itself — the workspace does, under its
  egress rules.
- `scripts/install-proot-apps.sh` and `scripts/install-appimages.sh` are now one
  driver, `scripts/cove-apps.sh`, mounted under both init names; both app kinds
  share one task queue per workspace.

- **Manage proot-apps in a running workspace.** Actions → Apps lists installed
  proot-apps, flags the ones with an update available (compared against ghcr.io
  without downloading), and installs, updates or removes them. Booting still
  never updates an installed app.
- **Background tasks in the navbar.** proot-apps installs, updates and removals —
  including the ones a workspace runs at boot — show in a tasks menu with
  progress and logs, across all of your running workspaces.
- **proot-apps and AppImages are desktop-only, end to end.** A workspace's type
  now follows its image, so one created while its image was mistyped (a curated
  app like VSCodium seeded as `desktop`) no longer offers the Apps dialog or
  launcher fields. The API refuses them for app, browser and link workspaces, a
  clone onto such an image drops them, and launch skips them on older rows.
  Catalog sync corrects a curated app still typed `desktop`. App workspaces now
  get the Apps step for packages and Docker (usable from the app's terminal).
- The stream iframe allows `screen-wake-lock`, which Selkies requests to keep
  the screen awake; it was logging a permissions-policy violation.
- The boot-time proot-apps install now runs entirely as the desktop user; it
  previously appended its log in `/config` as root.
- **Removed the in-browser thumbnail refresh.** An open workspace no longer
  opens a second stream socket every minute to update its grid card, which
  rarely produced a frame and made each workspace's Selkies server leak a pair
  of stats tasks per connection. Cards show the frame taken at launch. The
  SPA's CSP drops the `wss://*.<workspace domain>` connect source that existed
  only for it.
- **No connecting before the desktop is drawing.** A workspace can be opened only
  once its stream has produced its first frame (new `connectable` field; cards
  show STARTING, the stream page "Starting desktop"). Previously a workspace
  whose first frame took longer than the launch wait went running early, and a
  per-workspace app opened then needed a manual reload. The status monitor keeps
  retrying the frame, and streams Cove can't capture open after 90 seconds. The
  reconciler no longer stamps `preview_at` when it promoted without a frame.

## 1.1.0 — security hardening release

Upgrading from 1.0.x is in-place: pull, rebuild, `docker compose up -d`. The
database migrations are additive and idempotent, existing zones keep working,
and no new environment variable is required. Read **Behaviour changes** below
before upgrading a multi-user or multi-zone install.

### Behaviour changes to know about

- **The base compose file now binds plain HTTP to `127.0.0.1`.** It serves
  cleartext with Secure cookies off, which was only ever safe on the machine
  itself. If you deliberately run the base file on a trusted LAN, set
  `COVE_HTTP_BIND=0.0.0.0` in `.env`. The TLS overrides (`prod`, `lan-tls`,
  `dns`) publish `:443` on all interfaces as before.
- **Docker-in-Docker is now granted per account.** The deployment-wide toggle
  still exists, but a non-admin user must also be granted *Allow
  Docker-in-Docker* (Admin → Users). The nested daemon is privileged, which is
  host root; the toggle and the docs now say so plainly. Existing users start
  without the grant.
- **A remote zone is not usable until it is enrolled (mTLS).** Registering a
  zone with an endpoint no longer marks it `enrolled`; run the installer. The
  old plain-TCP fallback for un-enrolled zones is off unless
  `COVE_ALLOW_INSECURE_ZONES=true`.
- **A browser workspace's target URL no longer opens a hole through the LAN
  block.** Reaching a private-address site now requires the admin LAN policy
  (toggle + subnets + the workspace's LAN opt-in), like everything else.
- **Custom DNS servers must be public resolvers.** Private addresses are
  rejected because Docker's embedded resolver would forward to them from the
  host's network namespace, bypassing the egress guard.
- **Usernames may not start with `.`, `_` or `cove-`.** Those prefixes name
  Cove's own staging directories under the storage root. Existing accounts are
  untouched; new ones (local or SSO-derived) are validated.
- **The egress guard fails closed.** A workspace whose firewall could not be
  installed now ends in `error` instead of running unguarded.
- **Every workspace container gets a `pids_limit`** (default 8192, Admin →
  Settings, 0 = unlimited).
- **The access token is no longer persisted in `localStorage`.** Sessions
  resume from the httpOnly refresh cookie; nothing changes for users.

### Zone agents

An updated control plane keeps working with 1.0.x agents. Update agents
(Admin → Zones → *Update agent*) to get the new policy on the agent side.

- The Docker create policy is now an **allow-list** of `HostConfig` keys with
  case-insensitive, duplicate-rejecting parsing. It closes `VolumesFrom`,
  volume driver options, `DeviceCgroupRules`, `DeviceRequests`, `MaskedPaths`,
  `ReadonlyPaths`, `SecurityOpt` (other than `no-new-privileges`), `Sysctls`,
  container-mode PID/IPC namespaces and non-Cove networks and volumes.
- The proxy now also applies policy to `volumes/create`, `networks/create`,
  `networks/{id}/connect`, exec-create, `archive` and refuses `rename`. Exec is
  allowed only into workspace and routing-sidecar containers, plus the single
  fixed recreate command in the updater sidecar. Versioned paths can no longer
  reach unlisted API families such as `build` or `commit`.
- The central Traefik relays streams with a **separate per-zone edge cert**
  (`cove-edge-<id>`), so a relayed browser request can never satisfy the agent's
  control-plane CN pin. Existing zones get an edge cert automatically.
- Subpath-mode streams on remote zones now work: the control plane no longer
  strips the `/workspace/<id>` prefix before relaying, and hands the agent a
  per-request stream token (`X-Cove-Stream-Auth`) its own ForwardAuth verifies.
- Enrollment pins the endpoint to what the admin configured, and validates the
  CSR before consuming the single-use token. Zone `status` is no longer
  client-settable. Deleting a zone removes its staged keys from disk.

### Fixes

- The token minted by *change password* was rejected immediately (revocation
  compared microseconds against a whole-second `iat`).
- Production subpath mode: workspace routers had no TLS section, so streams
  answered 404 over HTTPS. `prod` and `dns` overrides now force TLS on the
  `websecure` entrypoint (as `lan-tls` already did), and the subpath router
  label itself carries `tls=true` on TLS deployments and on every remote zone
  (whose agent entrypoint is mTLS-only — its workspace routers never matched).
- The `dns` override fed the whole `.env` into Traefik; Cove's own secrets are
  now blanked in that container.
- The ForwardAuth and remote-zone config endpoints are refused by Traefik
  itself on the public entrypoints (HTTP 418 from its no-op service),
  regardless of the `Host` header.
- Stop/remove now also tear down a container by name, so a launch that failed
  before its id was recorded, or a halt landing mid-launch, no longer leaves a
  running container behind. Deleting an `error` workspace that still has a
  container goes through the manager.
- Start/stop transitions are conditional updates, so two concurrent starts can
  no longer launch two containers.
- Archive downloads and directory sizes no longer follow symlinks a user
  planted inside a workspace; clone refuses a pre-existing destination.
- Favicon lookups check every host they connect to, including redirect hops,
  and are bounded to 30 s. Preview capture caps what it reads from the
  container and the stripe sizes it decodes.
- OIDC: a string `groups` claim is no longer a substring match for the admin
  group; the issuer check no longer depends on the discovery document alone.
- Admin settings reject `Infinity` CPU limits (which broke every launch) and
  validate the GPU render node path.
- Passwords over bcrypt's 72-byte limit return 400 instead of 500.
- Tailscale `ts_exit_node` and `login_server` are validated; they were joined
  into `tailscale up` arguments unescaped.
- Helper scripts are staged atomically; the readiness probe no longer re-pulls
  its helper image every 10 s per stuck workspace.
- LAN-TLS override redirects HTTP to the published HTTPS port
  (`COVE_HTTPS_PORT`), and the CSP admits the subdomain-mode preview WebSocket.

### Still open (tracked, not in this release)

- Subpath routing mode frames streams same-origin with the app. An admin
  opening another user's workspace now gets an explicit warning, but the real
  fix is subdomain routing; multi-user installs should set
  `COVE_WORKSPACE_DOMAIN`.
- The stream signing key is shared by all agents. Per-zone derivation needs a
  re-enrollment step and is planned for 1.2.
- Per-user workspace quotas and zone-cert expiry monitoring/renewal.
