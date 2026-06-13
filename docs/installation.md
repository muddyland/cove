# Installation

Cove runs as a small Docker Compose stack:

- **Traefik** — reverse proxy that routes the control UI and every workspace stream, and authenticates streams via ForwardAuth.
- **docker-socket-proxy** (×2) — a write-capable proxy used only by the Cove backend to create/stop containers, and a read-only proxy used only by Traefik for service discovery.
- **cove** — the FastAPI backend + the built Vue SPA.

Workspace containers are launched on demand, each on its own isolated network. See **[../ARCH.md](../ARCH.md)** for the full topology.

## Requirements

- A Linux host with **Docker** and the **Docker Compose v2** plugin (`docker compose`).
- Access to the host Docker socket (the socket proxies mount it read-only).
- A few GB of free disk — desktop images (the LinuxServer webtop variants) are large and pull on first launch (a single webtop image is ~2 GB).
- For workspaces that route through a VPN or tailnet: `/dev/net/tun` must be available to containers.
- For public HTTPS: a domain pointing at the host with ports 80/443 reachable, **or** a supported DNS provider for the DNS-01 challenge. See [Deployment & HTTPS](deployment.md).

## Quick start (local / HTTP)

```bash
git clone <your-fork-url> cove
cd cove
cp .env.example .env
docker compose up --build -d
```

Then open <http://localhost>:

1. You land on the **first-run setup** screen — create the initial admin account (username + a password of at least 8 characters).
2. On first start, Cove auto-populates the image catalog from the LinuxServer.io API. If the host was offline, run **Admin → Images → Sync LinuxServer** later.
3. Click **Deploy Node** to launch a desktop, or **Open Website** to launch a browser pointed at a URL.

> The first launch of any image pulls it from `lscr.io` and can take several
> minutes. The workspace shows **Booting / Provisioning** until the container is
> ready.

The default `.env` serves plain **HTTP on port 80** and sets
`COVE_COOKIE_SECURE=false` so cookies work over `http://localhost`. **This is for
local use only** — for anything reachable over a network, serve HTTPS and set
`COVE_COOKIE_SECURE=true` (see [Deployment & HTTPS](deployment.md)).

## Building the image

The default Compose file builds the `cove:local` image from the repository
`Dockerfile` (a multi-stage build: Node compiles the frontend, then it's copied
into a Python 3.12 runtime). `docker compose up --build` rebuilds it. There is no
published image — you build locally.

## Day-to-day operations

```bash
docker compose logs -f cove          # backend logs
docker compose ps                    # stack status
docker compose down                  # stop the stack
docker compose up --build -d         # rebuild & restart (e.g. after a git pull)
```

- **Data** lives in `./data` (the SQLite database, the signing secret key, and — by default — workspace home directories). Back this up.
- **Persistent workspace homes** live under `COVE_STORAGE_PATH` (default `/var/lib/cove/workspaces`). See [Configuration → Storage](configuration.md#persistent-storage).

## Updating

```bash
git pull
docker compose up --build -d
```

Rebuilding recreates the `cove` container; running workspace containers are
independent and are not disturbed by a backend restart. Each workspace also
**pulls the latest image** the next time it is started.

## Resetting

To wipe all state (users, workspaces, audit log) and start fresh:

```bash
docker compose down
sudo rm -rf ./data           # deletes the DB, secret key, and default homes
# also remove persistent homes if you relocated them:
sudo rm -rf /var/lib/cove/workspaces
docker compose up --build -d
```

The next visit returns to the first-run setup screen.

## Production deployment

For HTTPS, OIDC, subdomain isolation, and DNS-01, continue to
**[Deployment & HTTPS](deployment.md)**. If you hit problems, see
**[Troubleshooting](troubleshooting.md)** — in particular the Docker daemon
`client version 1.24 is too old` fix that affects newer Docker Engine releases.
</content>
