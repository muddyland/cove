# Security model

A summary of how Cove isolates workspaces, protects credentials, and what it
assumes about its environment. For the deep architecture, see
**[../ARCH.md](../ARCH.md)**.

## Trust boundaries

- **The host is trusted.** Cove manages containers through a Docker socket proxy; anyone with host or Docker-daemon access has full control. Run Cove on a host you trust.
- **The Docker socket is never exposed directly.** Two filtered proxies sit in front of it: a **write-capable** proxy used only by the Cove backend (to create/stop containers), and a **read-only** proxy used only by Traefik (for service discovery, so a Traefik compromise can't create containers). Be clear about what "filtered" means for the write side: it can create privileged containers and exec into any container, so **a compromise of the Cove backend is a compromise of the host**. The read-only proxy can still read every container's environment.
- **Docker-in-Docker is host root.** The per-workspace nested daemon runs privileged; the feature is off by default, gated per account, and documented as equivalent to a root shell on the host. See [Deployment](deployment.md#docker-in-docker-development-inside-a-workspace).
- **Workspaces are semi-trusted.** A workspace owner already has a shell inside their own container, so the boundary that matters is *between* workspaces and *toward the control plane* — not within a single workspace.

## Workspace isolation

- Each workspace runs on **its own isolated Docker network** (`cove-ws-net-<id>`).
- Egress is firewalled to **WAN-only by default**. The Docker-internal range (`172.16.0.0/12`) and cloud-metadata range (`169.254.0.0/16`) are **always blocked**, so a workspace can never reach the Cove backend, the socket proxies, Traefik, the host metadata service, or other workspaces. See [Networking](networking.md).
- Containers drop all Linux capabilities (re-adding only a minimal set), run with `no-new-privileges` unless the workspace explicitly requested sudo and the admin hasn't force-disabled it, and carry a process limit. See [Workspaces → hardening](workspaces.md#sudo--container-hardening).
- The egress firewall **fails closed**: a workspace whose rules could not be installed ends in `error` rather than running unguarded, and connections opened before the rules landed are flushed.
- User-supplied values that reach command lines (Tailscale exit node and login server, package and app names, target URLs, DNS servers, GPU render node) are validated against strict shapes.
- **Managing apps inside a workspace** (the Apps dialog) runs the driver script as the **desktop user, never root** — `/config` is user-writable, so a root write there could be redirected through a planted symlink. Its output is treated as untrusted and re-validated by the backend, and calls are capped per workspace and per caller so a wedged container can't tie up server threads.
- **AppImage URLs are fetched by the workspace, never by Cove.** The control plane only validates and stores them, so a workspace can't use an install as a way to make the backend request an address it couldn't reach itself. proot-app update checks do run from the control plane, but only against a fixed registry repository and only for names in the LinuxServer catalog.

## Stream authentication

- Workspace streams are **never exposed unauthenticated**. Traefik's ForwardAuth calls back into Cove (`/api/auth/forward`) to authorize every stream request against your session, and that endpoint rejects any request whose `Host` isn't the internal authority, so it can't be probed from outside.
- In the default **subpath** mode, a stream is framed **same-origin** with the app, so code running inside that workspace can script against the page with the viewer's session. That is acceptable for a workspace you own; an admin opening another user's workspace is warned first. Multi-user installs should use subdomain isolation. The access token is held in memory only (never `localStorage`).
- In **subdomain-isolation** mode, the SPA session cookie is host-only and never reaches a workspace origin; streams use a separate, short-lived, single-workspace `cove_stream` token, handed off once via a one-time bootstrap token in the URL. A hostile workspace can at most obtain a credential for the very workspace its owner already controls. See [Deployment](deployment.md#per-workspace-subdomain-isolation).

## Credentials & secrets

- Passwords are hashed with **bcrypt**. Sessions use short-lived signed JWTs in **httpOnly** cookies; logout and password changes revoke all outstanding tokens.
- Per-user secrets — **SSH private keys, Tailscale auth keys, and Gluetun configs/keys** — are encrypted at rest with a Fernet key derived from the app signing secret before being written to the database. API responses mask them (presence booleans only).
- Optionally, the **entire SQLite database** can be encrypted at rest with SQLCipher via `COVE_DB_ENCRYPTION_KEY` (see [Configuration](configuration.md#at-rest-database-encryption)).
- OIDC ID tokens are verified with the signing algorithm pinned to an asymmetric allowlist (no `HS256`/`none`), and audience/issuer/nonce are all checked.

## Remote zones

- The control plane reaches a zone's Docker daemon only through the agent's mTLS port and a policy filter that allow-lists what a container may be created with, which containers may be exec'd into, and which volumes/networks may be created. See [Zones](zones.md#1-how-it-fits-together).
- Stream relays use a separate per-zone edge cert that the agent never accepts on its API or Docker paths.
- A zone is dialed only once enrolled; enrollment pins the endpoint an admin configured.

## Rate limiting & audit

- Login is rate-limited both in the application (per IP) and at the Traefik edge.
- Mutating cross-origin cookie-auth requests are rejected (CSRF protection).
- Security-relevant actions are recorded to an **audit log** (see [Administration → Audit log](administration.md#audit-log)).

## Operator responsibilities

- Always serve **HTTPS** and set **`COVE_COOKIE_SECURE=true`** when reachable beyond `localhost` — otherwise the session cookie travels in cleartext.
- Keep the signing secret (`./data/secret.key`) and any DB-encryption key safe and backed up; losing the DB-encryption key means losing the database.
- Restrict who can reach the host and the Docker daemon.
