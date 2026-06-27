# Cove — Zones (Remote Agent Nodes)

A **zone** is a remote host that runs workspace containers on behalf of a single
Cove **control plane**. Zones let one Cove instance reach into multiple network
segments: e.g. run the control plane in a DMZ while a **LAN zone** launches
workspaces that can reach LAN resources — without the control plane itself having
broad LAN access.

The control plane **dials** each zone over **mutual TLS** (one inbound port per
zone). A workspace is pinned to exactly one zone and can be **migrated** between
zones. Zone **Local** (id `0`) is the control plane's own Docker daemon and is
always present.

> **Status:** the agent stack is generated and validated by construction. The
> agent-side Traefik mTLS termination and the end-to-end stream path should be
> smoke-tested on your first real zone (see [Verifying](#verifying)).

---

## 1. How it fits together

```
        ┌─────────────────────────── control plane (e.g. DMZ) ──────────────────────────┐
        │  cove (FastAPI + SPA)   ── private CA, zone registry, enrollment, file/migrate │
        │  traefik                ── public ingress + ForwardAuth + HTTP dynamic provider │
        └───────────────┬───────────────────────────────────────────────────────────────┘
                        │  outbound mTLS to a SINGLE agent port (control plane dials)
                        │    • workspace streams · agent API · Docker API  → agent :8443
                        ▼
        ┌─────────────────────────── zone agent (e.g. LAN) ─────────────────────────────┐
        │  traefik (:8443)     ── one mTLS entrypoint; streams route locally, everything │
        │                          else → cove-agent                                     │
        │  cove (agent mode)   ── ForwardAuth · file/migration API · Docker proxy        │
        │      └─ Docker proxy ── create-policy filter → local docker-socket-proxy       │
        │  workspace containers ── the actual webtops, on per-workspace networks         │
        └───────────────────────────────────────────────────────────────────────────────┘
```

The agent runs the **same Cove image** with `COVE_AGENT_MODE=1` and exposes a
**single mutual-TLS port**. Everything the control plane needs — workspace streams,
the file/migration API, *and* the Docker API — rides that one port. The Docker
daemon is **never published on a network port**: the control plane's Docker client
talks to the `cove-agent` app, which **policy-filters** each `containers/create`
(rejecting `--privileged`, host namespaces, host-path bind mounts, the docker
socket, and disallowed caps/devices) before forwarding to the agent's *local*
`docker-socket-proxy`. So even the control plane's own credential can only create
Cove-shaped containers — it cannot trivially root the agent host.

**Trust model:** the control plane is a private CA (`data_dir/ca/`). At enrollment
it signs the agent's server certificate and issues itself a per-zone client
certificate. The agent's Traefik **requires a client certificate signed by this
CA** (`RequireAndVerifyClientCert`) and, via `passTLSClientCert`, the agent
additionally **pins the CN** to its own zone's client cert (`cove-cp-<id>`) — so a
client cert issued for one zone cannot be replayed against another. The control
plane verifies the agent's server cert against the same CA. The CA private key
never leaves the control plane, and the agent's server private key never leaves
the agent (only a CSR is sent).

---

## 2. Agent host requirements

- Linux host with **Docker** + the **Docker Compose v2** plugin (the installer
  installs Docker via `get.docker.com` if missing).
- `openssl`, `curl`, and `python3` (present on essentially all modern distros).
- **Outbound** reachability to the control plane's URL (to fetch the installer and
  enroll).
- **One inbound port** (default `8443`) reachable from the control plane — see
  [Networking](#4-networking--firewall).
- Disk for desktop images (webtops are large; pulled on first launch **on the
  agent**).
- The agent's storage path must match the control plane's — see
  [Storage parity](#5-storage-parity-important).

---

## 3. Setting up a zone

### 3a. Register the zone (control plane)

In the Cove SPA: **Admin → Zones → Add Zone**.

- **Name** — a label (e.g. `lan`).
- **Endpoint host** — the address the control plane will **dial** to reach the
  agent (the agent host's IP or DNS name, e.g. `10.0.0.5` or `agent.lan`). This is
  baked into the agent's server-cert SAN, so set it to the exact address the
  control plane uses.
- **mTLS port** (default `8443`) — the single port the control plane dials.

> Or via API: `POST /api/admin/zones` `{"name":"lan","endpoint_host":"10.0.0.5"}`.

### 3b. Mint an enrollment token

On the zone row click **Enroll** (API: `POST /api/admin/zones/{id}/enroll-token`).
You get a **single-use** token and a one-liner that embeds it:

```bash
curl -fsSL "https://cove.example.com/install.sh?token=<TOKEN>" | sudo bash
```

The plaintext token is shown **once** (only its SHA-256 is stored) and expires
after `COVE_ZONE_ENROLL_TOKEN_MINUTES` (default 60). It is consumed exactly once,
at enrollment.

### 3c. Run the installer on the agent host

Run the one-liner as root on the new node. It is idempotent and does the following
([`backend/server/routers/enroll.py`](../backend/server/routers/enroll.py) renders it):

1. Installs Docker if absent.
2. **Fetches the Cove agent image** from the control plane (`GET
   /api/zones/agent-image`, a `docker save` stream) and `docker load`s it — so a
   locally-built image with no registry still reaches the agent. Skipped when
   `COVE_ZONE_AGENT_IMAGE_FROM_CONTROL_PLANE=false` (registry pull instead).
3. Generates the agent's **server keypair + CSR locally** in
   `/var/lib/cove-agent/certs` (the private key never leaves the host) with the
   endpoint host in the SAN.
4. `POST`s the CSR to `/api/zones/enroll?token=…`; receives the **CA cert**, the
   **signed server cert**, the **stream-signing key**, the **workspace domain**,
   and the **pinned client-cert CN**.
5. Writes the agent stack to `/var/lib/cove-agent` (`.env`, `docker-compose.yml`,
   `traefik-dynamic.yml`) and runs `docker compose up -d`.

> **Image options.** Set `COVE_ZONE_AGENT_IMAGE` to the image the agent should run
> (default `cove:local`, matching the control plane's own image). By default the
> installer pulls those bytes **from the control plane** — no registry needed. If
> you publish Cove to a registry instead, set `COVE_ZONE_AGENT_IMAGE` to that ref
> and `COVE_ZONE_AGENT_IMAGE_FROM_CONTROL_PLANE=false` so the agent pulls it
> directly.

When it finishes the zone flips to **enrolled** and can run workspaces.

---

## 4. Networking & firewall

The control plane **initiates** all connections; the agent only needs **outbound**
access to the control plane plus **one inbound** port open **from the control
plane's address only**:

| Port | Direction | Purpose |
|---|---|---|
| `8443` (`endpoint_port`) | control plane → agent | Everything over one mTLS port: workspace streams, the agent file/migration API, and the Docker API (via the cove-agent proxy). |
| control plane URL (443)  | agent → control plane | Fetch installer, enroll, and (only for streams) the central ForwardAuth. |

For the **DMZ-cannot-reach-LAN** pattern, open a single tightly-scoped pinhole from
the DMZ control-plane host to the LAN agent's `8443`. The port requires a client
certificate signed by the Cove CA, and the Docker API behind it is policy-filtered
(no privileged/host-mount escapes), so the pinhole is not a broad exposure.

> The control plane reaches workspace streams by routing the workspace's
> subdomain/path to the agent's `8443` over mTLS via Traefik's HTTP dynamic
> provider (`/api/internal/traefik-config`). The central `traefik` service must
> mount the per-zone client certs — already wired in `docker-compose.yml` as
> `${COVE_DATA_DIR_HOST:-./data}/zone-certs:/zone-certs:ro`.

---

## 5. Storage parity (important)

Workspace `/config` is a **bind mount** resolved by the **agent's** Docker daemon
on the **agent's** filesystem. The path the control plane records must therefore
exist at the **same absolute path** on the agent. Set `COVE_STORAGE_PATH`
identically on the control plane and every agent (the installer passes it through
to the agent as `COVE_STORAGE_PATH` and mounts it into the agent container at the
same path). A mismatch makes bind mounts silently resolve to the wrong place.

The agent's file browser and migration endpoints operate under
`{COVE_STORAGE_PATH}/{username}/workspace-{name}` — the same layout as the control
plane.

---

## 6. The agent stack

The installer brings up these containers (compose project in
`/var/lib/cove-agent`):

| Service | Image | Role |
|---|---|---|
| `cove-agent-sockproxy` | `tecnativa/docker-socket-proxy` | The **local** (internal-only) Docker API the cove-agent proxy forwards to. Never published. |
| `cove-agent-sockproxy-ro` | `tecnativa/docker-socket-proxy` | Read-only API for the agent's Traefik to discover workspace containers. |
| `cove-agent` | `${COVE_ZONE_AGENT_IMAGE}` (the Cove image) | Agent-mode API: `/agent/auth/forward`, `/agent/files*`, `/agent/migrate/*`, and the **policy-filtered Docker proxy** (catch-all → local socket-proxy). Also defines the `cove-auth`/`cove-errors` middlewares the workspace routers reference. |
| `cove-traefik` | `traefik:v3.7` | The single mTLS entrypoint on `:8443` (`RequireAndVerifyClientCert`). Workspace streams route locally; everything else (agent API + Docker API) → `cove-agent`. |

There is no separately-exposed Docker port and no `ghostunnel`: the Docker API is
just another path on the one mTLS port, behind the create-policy filter.

The agent's Traefik **re-runs ForwardAuth** (`/agent/auth/forward`) as
defense-in-depth: even behind the mTLS port, a request must carry a valid Cove
stream token. The agent validates tokens with the **stream-signing key**
provisioned at enrollment — never the control plane's app secret — so a
compromised agent cannot forge session/refresh tokens.

---

## 7. Running workspaces on a zone

- **Launch:** the **Launch Workspace** and **Open Website** modals show a **Zone**
  selector once at least one remote zone is enrolled (it's hidden when only Local
  exists). Defaults to **Local**. (API: `zone_id` on `POST /api/workspaces`.)
  Websites opened on a remote zone are forced **ephemeral** — no browser data is
  written to the agent's storage.
- **Migration:** a stopped/errored workspace's card shows a **Migrate** button
  (when a remote zone exists) → pick a destination zone. Its `/config` is copied
  (relayed through the control plane), the zone pin flips, and the source copy is
  removed (copy-then-delete — a failure leaves the source intact). The workspace
  ends **stopped** on the destination; start it when ready. (API:
  `POST /api/workspaces/{id}/migrate` `{"target_zone_id": <id>}`.) The card shows a
  zone badge for any workspace not on Local.
- **Files:** the file browser proxies to the owning zone over mTLS
  (`/api/files?zone_id=<id>`).
- **Images:** image presence is per-daemon. Pull/inspect/remove per zone with the
  `zone_id` query param on the image endpoints (`/api/images/pull-status?zone_id=…`,
  etc.). The catalog metadata itself is shared.

> Only **enrolled** zones appear in the pickers. If you added an agent but don't
> see it, check **Admin → Zones** — it may still be `pending`/`enrolling`.

---

## 8. Operations

- **Liveness:** the status monitor pings each zone. An unreachable zone flips to
  **offline** (its workspaces are left as-is, not errored) and returns to
  **enrolled** when it answers again. `last_seen_at` tracks the last successful
  ping.
- **Rotate the control-plane client cert:** `POST /api/admin/zones/{id}/rotate-client-cert`.
  The control plane re-issues its own client cert (same CN, same CA) — no agent
  change needed.
- **Rotate the agent server cert / re-enroll:** mint a fresh token and re-run the
  installer on the agent; it regenerates the CSR and restarts the stack.
- **Remove a zone:** `DELETE /api/admin/zones/{id}`. Refused while any workspace is
  pinned to it — migrate or delete those first. The local zone (`0`) cannot be
  edited or deleted.

---

## 9. Configuration reference

Control-plane settings (env prefix `COVE_`):

| Variable | Default | Purpose |
|---|---|---|
| `COVE_ZONE_AGENT_IMAGE` | `cove:local` | The Cove image the agent runs. |
| `COVE_ZONE_AGENT_IMAGE_FROM_CONTROL_PLANE` | `true` | Installer fetches the image from the control plane (`docker save`/`load`) so a locally-built image needs no registry. Set `false` to have the agent pull a registry ref. |
| `COVE_ZONE_ENROLL_TOKEN_MINUTES` | `60` | Enrollment-token lifetime. |
| `COVE_ZONE_CERTS_MOUNT` | `/zone-certs` | Path **inside the Traefik container** where per-zone client certs are mounted. |
| `COVE_STREAM_SIGNING_KEY` | _(file)_ | Stream-token signing key. Generated as `data_dir/stream.key` on the control plane; **provided to agents** at enrollment. |
| `COVE_STORAGE_PATH` | _(unset)_ | Workspace storage root — must match on control plane and agents. |
| `COVE_WORKSPACE_DOMAIN` | _(unset)_ | Subdomain routing; provisioned to agents so they can resolve a workspace from its host. |
| `COVE_DATA_DIR_HOST` | `./data` | Host path of the control plane's data dir, mounted into Traefik for `zone-certs`. |

Agent-only settings (set by the installer, normally not edited by hand):

| Variable | Purpose |
|---|---|
| `COVE_AGENT_MODE=1` | Run as a zone agent (disables SPA/login/admin; enables the Docker proxy). |
| `COVE_STREAM_SIGNING_KEY` | The provisioned key, for local ForwardAuth. |
| `COVE_WORKSPACE_DOMAIN` | For resolving workspace public_id from the stream host. |
| `COVE_STORAGE_PATH` | Must equal the control plane's. |
| `COVE_AGENT_DOCKER_SOCKET_URL` | The local socket-proxy the Docker proxy forwards to (default `http://cove-agent-sockproxy:2375`). |
| `COVE_AGENT_EXPECTED_CLIENT_CN` | The only client-cert CN the agent accepts (`cove-cp-<zone-id>`); pins the zone to its control plane. |

---

## 10. Verifying

After enrollment, exercise the full path:

1. **Admin → Zones** shows the zone as **enrolled** with a recent *Last Seen*.
2. Pull an image onto the zone (Images, with the zone selected) — it appears on the
   agent's daemon.
3. Launch a workspace on the zone and **open its stream** — confirms the central →
   agent mTLS stream path and both ForwardAuth layers.
4. Browse/upload a file in that workspace — confirms the file proxy.
5. Migrate the workspace to another zone and back — confirms the relay.

---

## 11. Troubleshooting

| Symptom | Likely cause |
|---|---|
| Zone stuck **enrolling** | Installer didn't reach `POST /api/zones/enroll` (token expired, control-plane URL unreachable from the agent, or the inbound port isn't open). Re-mint the token and re-run. |
| Zone flips **offline** | Control plane can't reach `:8443` (firewall/pinhole, agent down, or cert mismatch). Check `docker logs cove-traefik` and `docker logs cove-agent` on the agent. |
| Workspace launches but **stream 502s** | Agent Traefik can't reach the container, or the central → agent mTLS route is misconfigured. Check agent `cove-traefik` logs and that `:8443` is open from the control plane. |
| Stream returns **401** | Token/cookie not reaching the agent ForwardAuth, or a stream-signing-key mismatch (agent must run with the provisioned `COVE_STREAM_SIGNING_KEY`). |
| Files/migration **409 "not enrolled for mTLS"** | The zone was manually registered (plain endpoint) but never enrolled — run the installer. |
| **403 "client certificate not authorized for this zone"** | The presented client-cert CN doesn't match the agent's `COVE_AGENT_EXPECTED_CLIENT_CN` (e.g. after a control-plane re-issue with a different CN, or wrong zone). Re-enroll to re-provision. |
| Bind mounts empty / wrong | `COVE_STORAGE_PATH` differs between control plane and agent (see [Storage parity](#5-storage-parity-important)). |

Useful on the agent: `docker compose -p cove-agent ps`, `docker logs cove-agent`,
`docker logs cove-traefik`.
