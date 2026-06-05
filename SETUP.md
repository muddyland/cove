# Cove — Setup & Installation

Cove runs as a small Docker Compose stack: **Traefik** (reverse proxy), a **docker-socket-proxy** (filtered Docker API), and the **cove** backend/SPA. Workspace containers are launched on demand on their own isolated networks.

## 1. Requirements

- Linux host with **Docker** and the **Docker Compose v2** plugin (`docker compose`).
- Access to the host Docker socket (the socket proxy mounts it).
- ~A few GB of disk for desktop images (webtop variants are large; they pull on first launch).
- For public HTTPS: a domain name pointing at the host (and ports 80/443 reachable, or a supported DNS provider for DNS-01).

## 2. Install (local / HTTP)

```bash
git clone <your-fork-url> cove
cd cove
cp .env.example .env
docker compose up --build -d
```

Then browse to <http://localhost>:

1. You'll land on the **first-run setup** screen — create the initial admin account.
2. On first start Cove auto-populates the image catalog from the LinuxServer.io API. If it was offline, go to **Admin → Images → ⟳ Sync LinuxServer**.
3. Click **Deploy Node** to launch a desktop, or **Open Website** to launch a browser pointed at a URL.

> The first launch of any image pulls it from `lscr.io` and can take a few minutes. The card shows **Starting** until the container is ready.

## 3. Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `COVE_HTTP_PORT` | `80` | Host port Traefik listens on (HTTP). |
| `TZ` | `UTC` | Timezone passed to workspace containers. |
| `COVE_COOKIE_SECURE` | `false` | Mark auth cookies `Secure`. **Set `true` in production (HTTPS).** |
| `COVE_STORAGE_PATH` | _(unset)_ | Host path for persistent workspace homes (see below). |
| `COVE_DOMAIN` | _(unset)_ | Public hostname (production HTTPS). |
| `COVE_ACME_EMAIL` | _(unset)_ | Let's Encrypt account email. |
| `COVE_ACME_DNS_PROVIDER` | _(unset)_ | Traefik DNS provider for DNS-01 (e.g. `cloudflare`). |
| `COVE_DB_ENCRYPTION_KEY` | _(unset)_ | Enables SQLCipher at-rest DB encryption when set. |

OIDC and provider-credential variables are documented inline in `.env.example`.

## 4. Persistent storage

Workspace home directories (each container's `/config`) live under
`${COVE_STORAGE_PATH}/<user>/workspace-<name>/`, defaulting to
**`/var/lib/cove/workspaces`**.

This path is bind-mounted into the `cove` container **at the same absolute path**
(see `docker-compose.yml`). That identity is required: the backend asks the
Docker daemon (which runs on the host) to bind-mount these dirs into workspace
containers, so the path must mean the same thing on the host and inside cove —
otherwise workspaces write to one place and the file browser reads another.

To relocate storage, set `COVE_STORAGE_PATH` to another absolute path; the
compose file already mounts `${COVE_STORAGE_PATH}` at the same path on both
sides, so no manual volume edit is needed:

```ini
# .env
COVE_STORAGE_PATH=/mnt/storage/cove
```

## 5. Production HTTPS

### TLS-ALPN challenge (ports 80/443 reachable)

```ini
# .env
COVE_DOMAIN=cove.example.com
COVE_ACME_EMAIL=you@example.com
COVE_COOKIE_SECURE=true
```
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

This adds the `:443` entrypoint, an HTTP→HTTPS redirect, the Let's Encrypt resolver, HSTS, and binds the app to your domain.

### DNS-01 challenge (closed ports, or wildcard certs)

```ini
# .env (in addition to the above)
COVE_ACME_DNS_PROVIDER=cloudflare
COVE_ACME_DNS_RESOLVERS=1.1.1.1:53,8.8.8.8:53
CF_DNS_API_TOKEN=your-scoped-cloudflare-token   # provider-specific
```
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.dns.yml up -d
```

Credential variable names depend on your provider — see the [Traefik DNS providers list](https://doc.traefik.io/traefik/https/acme/#providers). For a wildcard cert, set `COVE_DOMAIN=example.com` and uncomment the `tls.domains` labels in `docker-compose.dns.yml`.

## 6. OIDC / Authentik SSO

Set these in `.env` (then restart). When present, a "Sign in with …" button appears and is the primary login method; local login still works as a fallback.

```ini
COVE_OIDC_ISSUER=https://auth.example.com/application/o/cove/
COVE_OIDC_CLIENT_ID=...
COVE_OIDC_CLIENT_SECRET=...
COVE_OIDC_ADMIN_GROUP=cove-admins        # group claim → admin
COVE_OIDC_PROVIDER_NAME=Authentik        # button label
```

In your IdP, set the redirect URI to `https://<your-domain>/api/auth/oidc/callback`.

## 6b. Tailscale routing (per user)

Each user configures Tailscale under **Preferences → Tailscale** (no global key needed):

- **Auth key** — a Tailscale preauth key (stored against your account).
- **Login server** — optional custom control server (e.g. a Headscale URL).
- **Exit node**, **Accept routes**, **Accept DNS** — standard `tailscale up` options.

Then tick **Route through Tailscale** when launching a workspace. Cove starts a dedicated `tailscale/tailscale` sidecar for that workspace, and the workspace shares the sidecar's network namespace — so its egress goes out via your tailnet (and exit node, if set). The host needs `/dev/net/tun` available to containers. Sidecars and their state are removed when the workspace is halted/removed.

## 6b2. Per-workspace subdomain isolation (optional)

By default workspaces stream at a **subpath** (`/workspace/<id>/`) on the same
origin as the control UI. For stronger isolation you can give each workspace its
**own origin** so a workspace can never reach the SPA's session token:

```ini
# .env
COVE_WORKSPACE_DOMAIN=cove.example.com   # workspaces at <public_id>.cove.example.com
COVE_COOKIE_DOMAIN=cove.example.com      # so the session cookie reaches subdomains
```

This requires a **wildcard DNS record** (`*.cove.example.com`) and a **wildcard
TLS certificate** — deploy with the DNS-01 override and uncomment the wildcard
SAN labels in `docker-compose.dns.yml`:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.dns.yml up -d
```

Leave both unset to keep the (simpler) subpath routing — no wildcard needed.

## 6c. File browser

**Files** (in the top nav) lets each user browse, upload, download, and delete files within their own workspace storage area (`<storage>/<username>/workspace-*`). Access is confined to your directory; path traversal is rejected.

## 6d. Container lifecycle

Halting a workspace **removes** its container (and any Tailscale sidecar/network); starting it again **always pulls the latest image** first, so workspaces stay current. Persistent data in `/config` (your home dir) is untouched.

## 7. At-rest database encryption (optional)

Set `COVE_DB_ENCRYPTION_KEY` to a strong secret to encrypt the SQLite database with SQLCipher. Keep the key safe — losing it means losing the database. (Leave unset to run with a plain SQLite file.)

## 8. Operations

```bash
docker compose logs -f cove          # backend logs
docker compose ps                    # stack status
docker compose down                  # stop
docker compose up --build -d         # rebuild & restart after updates
```

- **Data** lives in `./data` (SQLite DB, secret key, and default workspace homes). Back this up.
- **Reset everything**: `docker compose down` then remove `./data` (this deletes users, workspaces, and homes).

## 9. Security notes

- The **docker-socket-proxy** is the only component touching the raw Docker socket; it exposes a filtered API to Traefik and Cove. Treat the host as trusted.
- Each workspace runs on its **own isolated network** with dropped Linux capabilities and `no-new-privileges`; workspaces cannot reach the Cove backend, the socket proxy, or each other.
- Workspace **streams are authenticated** by Traefik ForwardAuth against your Cove session — never exposed unauthenticated.
- Always run with HTTPS and `COVE_COOKIE_SECURE=true` when exposed beyond localhost.

## 10. Troubleshooting

| Symptom | Fix |
|---|---|
| Catalog is empty | Admin → Images → **Sync LinuxServer** (needs outbound internet). |
| Workspace stuck on **Starting** | First pull of a large image; check `docker compose logs -f cove` and `docker images`. |
| Login works but nothing happens | Ensure `COVE_COOKIE_SECURE=false` when testing over plain HTTP. |
| `502` on a workspace stream | The container is still booting or unhealthy; wait, or check the container logs (`docker logs cove-ws-<id>`). |
| **404 on everything + Traefik logs `client version 1.24 is too old`** | Very new Docker daemons (Engine 25+/API min ≥ 1.40) reject the API version Traefik probes with, so the Docker provider finds no routers. Re-enable the older API on the host daemon (see below). |

### Docker daemon `client version 1.24 is too old`

Recent Docker Engine raised its minimum API version, which breaks Traefik's
Docker provider (it probes with `/v1.24/...`). Re-enable backward compatibility
on the **host** daemon:

```bash
sudo mkdir -p /etc/systemd/system/docker.service.d
printf '[Service]\nEnvironment=DOCKER_MIN_API_VERSION=1.24\n' \
  | sudo tee /etc/systemd/system/docker.service.d/api-compat.conf
sudo systemctl daemon-reload
sudo systemctl restart docker      # briefly restarts all containers
```

After the daemon comes back, the Cove stack auto-restarts (restart: unless-stopped),
Traefik discovers the routers, and ACME issues the certificate on first request.
