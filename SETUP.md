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

By default, workspace home directories live under `./data/workspaces/<user>/workspace-<name>/`.

To store them elsewhere, set `COVE_STORAGE_PATH` and add a **matching** bind mount on the `cove` service in `docker-compose.yml` (same absolute path on both sides, so the backend and the Docker daemon agree):

```yaml
# docker-compose.yml → services.cove.volumes
- /mnt/storage/cove:/mnt/storage/cove
```
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
