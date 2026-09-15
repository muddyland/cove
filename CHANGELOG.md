# Changelog

## Unreleased

- **Manage proot-apps in a running workspace.** Actions → Apps lists installed
  proot-apps, flags the ones with an update available (compared against ghcr.io
  without downloading), and installs, updates or removes them. Booting still
  never updates an installed app.
- **Background tasks in the navbar.** proot-apps installs, updates and removals —
  including the ones a workspace runs at boot — show in a tasks menu with
  progress and logs, across all of your running workspaces.
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
