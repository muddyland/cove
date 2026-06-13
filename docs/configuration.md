# Configuration

Cove is configured in two places:

1. **Environment variables** (the `.env` file at the repo root) — deployment-level settings read at startup. **Changing them requires a restart** (`docker compose up -d`).
2. **Runtime settings** under **Admin → Settings** — stored in the database, editable from the UI, and applied to newly started workspaces **without a restart**.

This page documents both. For HTTPS/TLS specifics see [Deployment & HTTPS](deployment.md); for OIDC see [Authentication](authentication.md).

---

## Environment variables

All Cove variables use the `COVE_` prefix and are read from `.env` (or the real
process environment). Empty values are treated as unset. The annotated starter
file is [`.env.example`](../.env.example).

### Core / networking

| Variable | Default | Purpose |
|---|---|---|
| `COVE_HTTP_PORT` | `80` | Host port Traefik's HTTP entrypoint binds to. |
| `TZ` | `UTC` | Timezone; passed through to workspace containers as `COVE_WORKSPACE_TZ`. |
| `COVE_COOKIE_SECURE` | `true` (code) / `false` (starter `.env`) | Mark auth cookies `Secure` (HTTPS-only). Keep `false` only on `http://localhost`; **set `true` for any networked deployment**, or browsers drop the cookies. |
| `COVE_DATA_DIR` | `/app/data` | In-container path for the SQLite DB and signing key. Bind-mounted from `./data`. |
| `COVE_STORAGE_PATH` | `/var/lib/cove/workspaces` | Host path for persistent workspace homes. Must be identical on host and inside the container — see [Persistent storage](#persistent-storage). |
| `COVE_DNS_PRIMARY` | `1.1.1.1` | Primary resolver pinned for the **cove** container itself. |
| `COVE_DNS_SECONDARY` | `9.9.9.9` | Secondary resolver for the cove container. Set both to your LAN DNS if internal services (e.g. a split-horizon OIDC issuer) only resolve there. |

> **Why pin DNS for the cove container?** Docker strips loopback nameservers
> (systemd-resolved/dnsmasq) when generating a container's `resolv.conf`. If the
> host is in that state, the cove container can end up with no working upstream
> DNS, and OIDC discovery or the LinuxServer API fail with "Temporary failure in
> name resolution". Pinning a resolver avoids this.

### Production HTTPS

| Variable | Default | Purpose |
|---|---|---|
| `COVE_DOMAIN` | _(unset)_ | Public hostname (used with `docker-compose.prod.yml`). |
| `COVE_ACME_EMAIL` | _(unset)_ | Let's Encrypt account email. |
| `COVE_ACME_DNS_PROVIDER` | _(unset)_ | Traefik DNS provider code for the DNS-01 challenge (e.g. `cloudflare`). |
| `COVE_ACME_DNS_RESOLVERS` | _(unset)_ | Resolvers used to check the DNS-01 challenge, e.g. `1.1.1.1:53,8.8.8.8:53`. |
| _provider creds_ | _(unset)_ | Provider-specific, read straight from the environment by Traefik (e.g. `CF_DNS_API_TOKEN`). |

### Subdomain isolation

| Variable | Default | Purpose |
|---|---|---|
| `COVE_WORKSPACE_DOMAIN` | _(unset)_ | When set, each workspace streams at `<public_id>.<domain>` — its own origin. Unset = subpath routing (`/workspace/<id>/`). Requires wildcard DNS + cert. See [Deployment](deployment.md#per-workspace-subdomain-isolation). |
| `COVE_APP_ORIGIN` | _(unset)_ | Public origin of the SPA (e.g. `https://cove.example.com`); used for cross-origin framing CSP in subdomain mode. Set automatically by `docker-compose.prod.yml`. |

### Authentication & OIDC

| Variable | Default | Purpose |
|---|---|---|
| `COVE_OIDC_ISSUER` | _(unset)_ | OIDC issuer URL. OIDC is enabled only when issuer **and** client ID **and** secret are all set. |
| `COVE_OIDC_CLIENT_ID` | _(unset)_ | OIDC client ID. |
| `COVE_OIDC_CLIENT_SECRET` | _(unset)_ | OIDC client secret. |
| `COVE_OIDC_SCOPES` | `openid email profile groups` | Scopes requested at login. |
| `COVE_OIDC_ADMIN_GROUP` | _(unset)_ | Group claim that grants admin. When set, admin status is synced from the token on every login. |
| `COVE_OIDC_PROVIDER_NAME` | `SSO` | Label shown on the login button. |
| `COVE_OIDC_ONLY` | `false` | Disable local login + setup entirely (SSO only). Only takes effect when OIDC is fully configured, so a broken config can't lock you out. |

See [Authentication](authentication.md) for the full SSO flow and recovery steps.

### Tokens, cookies & limits (advanced)

Sensible defaults; rarely changed.

| Variable | Default | Purpose |
|---|---|---|
| `COVE_ACCESS_TOKEN_MINUTES` | `30` | Access-token / session-cookie lifetime. |
| `COVE_REFRESH_TOKEN_DAYS` | `7` | Refresh-token lifetime. |
| `COVE_STREAM_TOKEN_MINUTES` | `480` | Per-workspace stream-cookie lifetime (subdomain mode). |
| `COVE_STREAM_BOOTSTRAP_MINUTES` | `5` | Lifetime of the one-time stream bootstrap token in the URL. |
| `COVE_LOGIN_RATE_LIMIT` | `10` | Login attempts allowed per window, per IP (application-level). |
| `COVE_LOGIN_RATE_WINDOW_SECONDS` | `60` | The login rate-limit window. |
| `COVE_MAX_UPLOAD_MB` | `1024` | Maximum size of a single file-browser upload (MiB). |
| `COVE_WORKSPACE_PUID` | `1000` | UID applied to the workspace desktop user (`PUID`). |
| `COVE_WORKSPACE_PGID` | `1000` | GID applied to the workspace desktop user (`PGID`). |
| `COVE_JWT_ALGORITHM` | `HS256` | JWT signing algorithm for Cove-issued tokens. |
| `COVE_SECRET_KEY_PATH` | `<data_dir>/secret.key` | Path to the signing-secret file (auto-generated, `0600`). |

> Traefik also enforces an **edge** rate limit on `/api/auth/login`
> (~5 req/min average, burst 10), independent of the application limiter above.

### Internal / Compose-managed

These are set by the Compose files and normally need no change:
`DOCKER_HOST`, `DOCKER_API_VERSION` (default `1.41`), `FORWARDED_ALLOW_IPS`,
`COVE_TRAEFIK_NETWORK` (`cove-net`), `COVE_TRAEFIK_CONTAINER` (`cove-traefik`),
and `COVE_FORWARD_AUTH_HOST` (`cove:8080` — the internal authority that
`/api/auth/forward` requires, so the ForwardAuth endpoint can't be probed
externally).

### At-rest database encryption

| Variable | Default | Purpose |
|---|---|---|
| `COVE_DB_ENCRYPTION_KEY` | _(unset)_ | When set, encrypts the SQLite database with SQLCipher. |

Keep the key safe — **losing it means losing the database**. Independently of
this, individual secrets (SSH private keys, Tailscale auth keys, Gluetun configs)
are always encrypted at rest with a Fernet key derived from the app signing
secret before being written to the DB.

---

## The Compose files

Cove layers Compose files; later files override earlier ones.

| File | Purpose |
|---|---|
| `docker-compose.yml` | Base stack: the two socket proxies, Traefik (HTTP on `:80`), and the cove app. Serves plain HTTP. |
| `docker-compose.prod.yml` | Adds the `:443` entrypoint, HTTP→HTTPS redirect, the Let's Encrypt resolver, HSTS, and binds the app to `COVE_DOMAIN`. |
| `docker-compose.dns.yml` | Switches ACME to the DNS-01 challenge (for closed ports or wildcard certs). |

Examples:

```bash
# Local HTTP
docker compose up -d

# Production HTTPS (TLS-ALPN)
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Production HTTPS (DNS-01 / wildcard)
docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.dns.yml up -d
```

---

## Persistent storage

Each workspace's home directory (the container's `/config`) is stored on the host at:

```
{COVE_STORAGE_PATH}/{username}/workspace-{sanitized-name}/
```

defaulting to `/var/lib/cove/workspaces`. The workspace name is sanitized
(non-`[a-zA-Z0-9_-]` → `-`, lowercased) for the directory component.

This path is bind-mounted into the cove container **at the same absolute path**.
That identity is required: the backend asks the host's Docker daemon to bind-mount
these directories into workspace containers, so the path must mean the same thing
on the host and inside cove — otherwise workspaces write to one place and the file
browser reads another. To relocate storage, just set `COVE_STORAGE_PATH`; the
Compose file already mounts it at the same path on both sides.

```ini
# .env
COVE_STORAGE_PATH=/mnt/storage/cove
```

**Ephemeral** workspaces get no bind mount — their `/config` lives in the
container's writable layer and is discarded on halt. See
[Workspaces → Storage](workspaces.md#persistent-vs-ephemeral-storage).

---

## Runtime settings (Admin → Settings)

These live in the database, not in `.env`. Edit them at **Admin → Settings**;
they apply to **newly started** workspaces (existing running ones keep their
current limits). Full details in [Administration](administration.md#settings).

| Setting | Default | Controls |
|---|---|---|
| Default CPU limit (cores) | `0` (unlimited) | Per-container CPU cap; fractions allowed. |
| Default memory limit (MB) | `0` (unlimited) | Per-container RAM cap. |
| Max runtime (hours) | `24` (`0` = unlimited) | Auto-stop running workspaces older than this. |
| Force-disable sudo | `off` | Apply `no-new-privileges` to **all** workspaces, overriding the per-launch "Allow sudo". |
| LAN access (master toggle) | `off` | Allow workspaces to reach the LAN directly (per-workspace opt-in still required). |
| Allowed LAN subnets | _(empty)_ | IPv4 CIDRs reachable when LAN access is on. |
| Tailscale sidecar image | `tailscale/tailscale:latest` | Image used for the per-workspace Tailscale sidecar. |
| Gluetun sidecar image | `qmcgaw/gluetun:latest` | Image used for the per-workspace Gluetun VPN sidecar. |

The same page shows a **read-only summary** of the env-configured settings above
(domain, cookie-secure, token lifetimes, OIDC status, DB-encryption status, …) so
you can confirm what the running process picked up. Secrets are never shown — only
whether they are present.
</content>
