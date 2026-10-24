# Changelog

## Unreleased

- **Saved apps install on first boot on every zone, and the dialog can install
  them all.** A migration doesn't copy app files, so a workspace's first boot on
  the new zone reinstalls them from its saved lists. That install was an init
  script bind-mounted from the zone host's own copy of Cove's scripts, staged by
  its agent. An agent older than the script had no copy, Docker mounted an
  empty directory in its place, and nothing installed: every app had to be
  installed again one by one. Now the control plane queues the missing apps
  through the workspace's shell (as the Apps dialog already does) once the
  desktop is up. It's one background task per kind, marked **saved apps** in
  the tasks menu, which now checks for it as soon as the workspace comes up.
  The Apps dialog counts saved apps that aren't installed, lists missing
  AppImages too, and has **Install all**. Control-plane change: deploy only.
- **Agent updates ship the control plane's own image.** Updating a zone's agent
  pushed whatever image was tagged `cove:local` on the control plane host (the
  `COVE_ZONE_AGENT_IMAGE` default), not the image the control plane runs. A
  control plane deployed from a registry image doesn't run `cove:local`, so
  Update and re-enrolling kept shipping an old agent, and GPU workspaces on zones
  kept failing with *"blocked by Cove agent policy: device /dev/dri/renderD129 is
  not allowed"* even though the policy allowing render nodes had shipped. Unless
  `COVE_ZONE_AGENT_IMAGE` is set, the agent image is now the exact image the
  control plane container runs. Update tags it on the zone as the ref the zone's
  compose file runs, so existing zones need no reinstall. Zone health shows
  **agent outdated** while an agent runs a different build. A launch refused by
  a zone's agent policy now says to update the agent, and Update fails up front
  when the control plane can't find an image to ship. **Operator action:** deploy,
  then **Update** each zone's agent.
- **GPU and Docker-in-Docker settings per zone.** Each zone host has its own
  GPU, but GPU used the control plane's global toggle, render node and group
  for every zone, so a zone whose GPU sits at a different node (or a zone that
  shouldn't use GPU at all) couldn't be set up. Docker-in-Docker was local-only,
  and a DinD workspace migrated to a zone could no longer be edited: every save
  failed with *"Docker-in-Docker is only available on the local zone"*, and the
  edit form hid the Docker toggle there, so it couldn't be turned off either.
  Now **Admin → Zones → Settings** sets a zone's GPU (inherit/on/off, render
  node, fallback GID) and **Allow Docker-in-Docker on this zone**. Because the
  DinD sidecar is privileged (root on the zone host), the agent also needs its
  host's own opt-in (`COVE_AGENT_ALLOW_DIND`, which re-running the installer
  sets from the zone setting); with it, the agent accepts exactly one privileged
  shape, the `cove-dind-<id>` sidecar in a workspace's netns. The launch and
  edit forms ask for the policies of the workspace's own zone. A workspace that
  already has DinD stays editable on a zone without it, and migrating a DinD
  workspace to such a zone is refused with a clear message. **Operator action:**
  deploy, then **Update** each zone's agent. To use DinD on a zone, turn it on
  in the zone's settings, click **Enroll** and re-run the installer on that host.

## 1.3.1 — Pillow security fixes and remote-zone repairs

- **GPU workspaces on remote zones use the render node's real group.** Before a
  GPU launch Cove checks which group owns the render node, so the workspace
  user can open it. On zones that check bound the host's `/dev`, which the
  agent policy refuses, so Cove silently fell back to the GID in Admin →
  Settings, and a wrong value there meant a stuttering, software-encoded
  stream. On zones the check now maps just the render node (which the agent
  allows) and reads its group; a missing node now fails the launch with a clear
  message there too. Uses the render-node policy below, so zones need that
  agent update.
- **Remote-zone workspaces come up promptly and show previews.** Every Docker
  exec on a zone went through the agent's reverse proxy, which can't carry
  docker-py's upgraded exec socket, so exec output came back empty or garbled.
  Workspaces sat on *Starting container…* while the first-frame capture timed
  out, then on *Waiting for the first frame…* until the 90-second fallback, and
  the Files browser, app installs and Tailscale status misbehaved on zones too.
  Execs now start as plain HTTP requests, which any proxy relays (control-plane
  change only; agents need no update).
- **GPU workspaces launch on remote zones.** The zone agent's Docker policy only
  allowed the TUN device, so a GPU workspace failed with *"blocked by Cove agent
  policy: device /dev/dri/renderD128 is not allowed"*. It now also allows DRI
  render nodes (`/dev/dri/renderD<N>`, same path in the container); card/primary
  nodes and every other device stay blocked. Update each zone's agent
  (Administration → Zones → Update agent) to pick it up.
- **Zones connect again on Python 3.14.** Since the image moved to Python 3.14,
  the control plane's TLS clients verify with `VERIFY_X509_STRICT`, which
  rejects certs without key identifiers, so every zone showed unreachable
  (`certificate verify failed: Missing Authority Key Identifier`). The zone CA
  now gets a Subject Key Identifier (an existing CA is re-signed in place with
  the same key) and every leaf carries Subject and Authority Key Identifiers.
  **Re-enroll each existing zone once** after upgrading: its agent server cert
  and stored CA copy were issued without them.

### Security and maintenance

- **Pillow 13.0.0** (from 12.3.0) fixes 41 advisories, six of them critical:
  heap overflows in the JPEG, FlashPix and TIFF code (GHSA-8g5g-8vmr-9782,
  GHSA-5x8w-8jj5-q52h, GHSA-332p-7hrc-92wf), stale image storage reused by
  multi-frame loaders (GHSA-v9pv-5gc8-w2m6) and argument injection into
  Ghostscript (GHSA-w889-685w-c8fh). The rest are denial-of-service and memory
  disclosure bugs in Pillow's decoders. Cove decodes favicons it downloads from
  the sites that browser workspaces point at, so a hostile site could reach these
  decoders. Upgrading the image is the whole fix.
- **Frontend build tooling:** Vite 6.4.5, Vitest 4.1.12, serialize-javascript
  7.1.4 and fast-uri 3.1.9 (launch-editor command injection on Windows, Vite dev
  server file access, Vitest mock injection, a serialize-javascript XSS and
  fast-uri parsing issues). All dev-only; none ship in the image. They also turn
  the dependency audit green again, and while it was red no image was built.

## 1.3.0 — VPN profiles, in-stream files and a usability pass

- **System info on the stream page.** The separate VPN and Tailscale indicators
  and the CPU/memory gauge are now one **System info** button (it keeps a green
  lock / network icon when a route is up). The dialog adds what was missing:
  uptime and auto-stop with Extend, processes, network and disk I/O, the real
  container limits, the VPN profile / provider / servers or tailnet IP /
  hostname / exit node with the sidecar's health and restarts, LAN access, the
  public IP the workspace really exits from (on request), home folder type and
  size, and container details (image ID, restarts, zone, options, hardening).
- **Files on the stream page.** Drop files onto the stream to copy them into the
  workspace (`~/Desktop`, or `~/Downloads` for browser/app workspaces), with
  progress and no overwrites. **Menu → Files** browses the home folder, downloads
  files, makes folders and uploads (button or drop). It works through the
  container, so ephemeral and remote-zone workspaces are covered, and files
  belong to the workspace user.

- **The browser extension understands VPN and Tailscale profiles** (Open in
  Cove 0.2.0). Since profiles landed, the extension still read the removed
  single-config endpoints, so VPN and Tailscale showed as unavailable in its
  launcher for everyone. It now lists your profiles under the network choice,
  disables incomplete ones with the reason, warns when a VPN profile is already
  connected elsewhere (and pre-selects a free one), sends the chosen profile,
  and remembers it by name. Against an older Cove it falls back to the single
  config. Also fixes "Connecting to Cove…" lingering under the loaded form.
- **The extension download moved to the user menu** (your account name, top
  right → **Browser extension**), out of Preferences: it is a download, not a
  setting. It opens the same instructions in a dialog, and the item only shows
  when this build ships the extension.

- **Several VPN and Tailscale profiles per user.** Preferences now keeps a list
  of **VPN profiles** and **Tailscale profiles** instead of one config each, and
  you pick the profile per workspace when launching or editing it. A VPN profile
  can come from an uploaded config file (as before) or from one of Gluetun's
  built-in providers (Mullvad, Proton VPN, AirVPN, NordVPN, Surfshark,
  Windscribe, IVPN, PIA and others): pick the provider, paste the key or login,
  optionally choose countries / cities / servers — no file needed. The "one VPN
  workspace at a time" rule is now **one connection per profile**, so two keys
  for the same provider can be connected at once. Each profile shows whether it
  is ready (or what it is missing), which workspaces use it, and where it is
  connected; one still in use can't be deleted. The dashboard card names the
  profile and, when a start is blocked, which workspace holds it.
  *Upgrade:* each user's existing Gluetun and Tailscale config becomes a profile
  named "Default" that their existing workspaces point at. The old
  `GET/PUT /api/users/me/gluetun` and `/me/tailscale` endpoints are replaced by
  `/api/users/me/vpn-profiles` and `/me/tailscale-profiles`; creating a workspace
  with just `use_gluetun` / `use_tailscale` still works and uses the first profile.
- **Files rows are less crowded.** The five per-row icon buttons (download, copy,
  cut, trash, delete) are now a download button plus a **⋯** menu, which right-
  clicking the row also opens at the cursor. Trash rows keep **Restore** and move
  the permanent delete into the menu. The menu is shared with the Images page,
  renders outside the table's scroll box so it is never clipped, flips up near
  the bottom of the screen, keeps one menu open at a time, and works from the
  keyboard (arrows, Home/End, Enter, Esc).
- **The dashboard no longer shows a dead workspace as running.** The grid only
  re-read the list while something was starting or stopping, so a workspace that
  crashed, hit the max-runtime auto-stop, or was stopped from another device or by
  an admin kept its RUNNING badge, a CONNECT button and its last CPU/memory until
  a reload. The list now refreshes all the time (3 s while anything is changing,
  10 s otherwise), pauses in a background tab and refreshes on return, survives a
  failed poll with back-off, and drops the stats of anything not running.
- **Connecting to a workspace that died says why, within seconds.** The stream
  page re-reads the workspace while it waits for the route, so the real error
  replaces the spinner instead of a generic "taking longer than usual" after 25 s.
  A workspace stopped or deleted while its page is open shows that (with Start /
  Back) instead of a frozen frame.
- **The first start of an image shows its download.** Image pulls are streamed and
  their progress surfaces on the card, the stream page and the Images page
  ("Downloading image · 1.2 GB / 4.7 GB"). The wizard marks images that aren't on
  the chosen zone yet and warns before launching one.
- **Failed workspaces get their own "Needs attention" section** at the top of the
  grid, with Logs available on them (it was only offered while running).
- **One set of words for workspace state:** Starting / Running / Stopping /
  Migrating / Stopped / Error, and Start / Connect / Stop / Delete — instead of
  Online/Offline, Boot, Halt and Purge/Terminate depending on the screen.
- **Cards say more:** uptime or "last used", a countdown to the max-runtime
  auto-stop with **Extend** in its last hour (new `POST /workspaces/{id}/extend`),
  and chips for network, persistent/ephemeral, LAN, sudo, Docker and GPU.
- **Admin "Halt" on the sessions page deleted the workspace.** It called the
  delete path, so the owner lost the workspace and its settings. It now stops it
  like the owner's Stop; the page is renamed **Running** and shows owner, zone and
  uptime.
- **Audit log:** workspace entries show the workspace name, with filters (user,
  action or action group, date range, text), paging and CSV export.
- **Images:** size, when it was last pulled, how many workspaces use it, and a
  per-image "Check for update" against the registry. Destructive actions moved
  into a row menu.
- **Zones:** a live health check per zone (reachable, latency, engine, running
  containers); the local zone reads "this host" instead of dashes.
- **Users:** "Sign out" invalidates every login of a user.
- **Timestamps** from the API are naive UTC and were being shown as local time;
  they are now parsed as UTC everywhere.
- **Styling:** secondary text contrast raised from ~2.8:1 to ~5.8:1 and small text
  sized up; native controls (file pickers, selects, scrollbars) render dark; table
  rows no longer have a broken border under the actions column; content pages are
  capped to a readable width; Preferences and Settings use the width (Settings
  gets section links and a sticky Save with an unsaved-changes hint); the ADMIN
  menu matches the other nav items; the wizard's step labels no longer truncate on
  phones; field labels lost their `//` prefixes.
- **Fonts are bundled** (Orbitron, Share Tech Mono via fontsource) instead of
  loaded from Google Fonts, so the UI looks right on networks without internet and
  no longer contacts Google on every page load. The CSP drops the Google hosts.

### Security and maintenance

- **python-jose is replaced by PyJWT.** python-jose 3.5.0 lets a DER-encoded
  public key slip past its algorithm-confusion guard (CVE-2024-33663, 9.1) and
  has no fixed release. PyJWT 2.15.1 is a drop-in for what Cove uses; OIDC still
  accepts only the asymmetric allowlist, and app tokens keep HS256 and the same
  claims, so sessions issued before the upgrade stay valid.
- **Frontend dependencies:** Vue 3.5.43 (the `@vue/server-renderer` attribute
  XSS, GHSA-g2v6-rqmx-r4w6 — only reachable in the tests, since Cove is a
  client-rendered SPA), source-map-js 1.2.2, brace-expansion 2.1.7 / 5.0.12 and
  fast-uri 3.1.8. All build-time or dev-only.
- **Python 3.14.** The image, CI and ruff target move from 3.12. The image can
  also be built on a Docker Hardened Image (no shell, no package manager): layer
  `docker-compose.dhi.yml` onto a stack, or set `USE_DHI=true` in CI. Slim stays
  the default. The runtime stage runs no commands now, the app starts as
  `python -m uvicorn` and runs as root explicitly. The image build honours
  `PIP_INDEX_URL` and `NPM_CONFIG_REGISTRY`.
- **CI:** Docker client and dind on 29.8.2 (builds pass `--provenance=false
  --sbom=false` so the registry gets a single manifest), and job and base images
  come straight from Docker Hub. The backend suite runs in about 3½ minutes
  instead of 10 and no longer prints warnings (uvicorn 0.54.0, httpx2 for the
  test client).

## 1.2.0 — apps, browsers and provider-generic SSO

- **Screen previews work again on current LinuxServer images, which also unblocks
  connecting.** Selkies moved its stream socket from `/websockets` to
  `/api/websockets` and 404s the old path, so every capture came back empty: no
  screenshot on any card, and — since a workspace is held back from connecting
  until its first frame exists — every launch sat at **Online / STARTING** for the
  full 90-second fallback before it would open. Cove now tries the current
  endpoint first and falls back to the legacy one, so it spans images on either
  side of the change.
  - The capture also joins as `?role=viewer` where that exists: a connection the
    stream will not promote to primary, verified against a live session (the
    watcher kept streaming, where a plain connection triggers Selkies' "KILL a new
    primary client connected"). It can only take a frame the stream is already
    broadcasting, so starting one still means connecting as primary.
  - **A capture that fails now says so.** A non-zero exit from the in-container
    client was read as "wrong interpreter, try the next one", which is exactly how
    a moved endpoint stayed invisible at every log level above DEBUG. A client
    that runs and dies, or an endpoint that answers nowhere, is a warning naming
    the workspace and the reason.
- **The in-stream Selkies menu is themed again.** Its dashboard was rebuilt around
  primitive design tokens (`--bg`, `--surface`, `--border-soft`, `--text`,
  `--accent`) with the old `--sidebar-*` variables derived from them, so overriding
  only the derived ones left everything newer — the stream-stats tiles, meters and
  graphs, the files modal, the gamepad tester — reading stock colours next to
  Cove's. The injected theme now sets the primitives too (and the `--stat-*`
  colours the stats panel scopes to itself), so current components inherit the
  palette and future ones will as well.

- **OIDC works with any provider, not just Authentik.** Everything beyond the
  issuer and client credentials is now read from the issuer's discovery
  document, so Kanidm, Authentik, Keycloak, Entra ID, Okta and Google all work
  from the same three settings. Concretely:
  - **PKCE (S256)** is used whenever the provider advertises it, with the
    verifier held in an httpOnly cookie. Kanidm *requires* PKCE and previously
    rejected the login outright.
  - **Token-endpoint client auth** follows `token_endpoint_auth_methods_supported`,
    preferring HTTP Basic (the OIDC default, and the only method Kanidm accepts).
    Cove previously always posted the secret in the request body.
  - **Group matching** accepts every shape providers emit — bare names, SPNs
    (`cove-admins@idm.example.com`), Keycloak paths (`/cove-admins`), object
    entries and delimited strings — case-insensitively and always whole-value,
    never as a substring. `COVE_OIDC_ADMIN_GROUP` takes a comma-separated list,
    and `COVE_OIDC_GROUPS_CLAIM` takes a dotted path for nested claims
    (Keycloak's `realm_access.roles`).
  - **Usernames** come from a configurable claim list
    (`COVE_OIDC_USERNAME_CLAIMS`) and `user@realm` is trimmed to `user`, so a
    Kanidm SPN no longer becomes `alice-idm-example-com`.
  - **The groups scope is only requested when an admin group is configured**, so
    providers that reject unknown scopes don't fail login over data Cove has no
    use for.
  - **Userinfo claims are merged in** for providers that expose group membership
    only there — underneath the verified ID-token claims, and only when the
    subject matches, so a bearer-fetched claim can never override a verified one.
  - **Discovery and JWKS are cached with a TTL** (`COVE_OIDC_METADATA_TTL_SECONDS`)
    instead of forever, and an unknown `kid` forces one refetch — provider
    signing-key rotation no longer locks everyone out until a restart.
  - **Issuer mismatch fails loudly** at discovery instead of quietly accepting
    either the configured or the advertised issuer at verification time.
  - Startup logs the negotiated PKCE, client-auth method and scopes, which is the
    first thing to look at when SSO breaks after changing providers.

  Existing Authentik deployments need no config change. `COVE_OIDC_SCOPES` now
  defaults to `openid email profile` (the groups scope is appended automatically),
  and the `COVE_OIDC_PROVIDER_NAME` default in `docker-compose.yml` is now `SSO`
  rather than `Authentik`.

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
