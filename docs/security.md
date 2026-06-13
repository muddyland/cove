# Security model

A summary of how Cove isolates workspaces, protects credentials, and what it
assumes about its environment. For the deep architecture, see
**[../ARCH.md](../ARCH.md)**.

## Trust boundaries

- **The host is trusted.** Cove manages containers through a Docker socket proxy; anyone with host or Docker-daemon access has full control. Run Cove on a host you trust.
- **The Docker socket is never exposed directly.** Two filtered proxies sit in front of it: a **write-capable** proxy used only by the Cove backend (to create/stop containers), and a **read-only** proxy used only by Traefik (for service discovery, so a Traefik compromise can't create containers).
- **Workspaces are semi-trusted.** A workspace owner already has a shell inside their own container, so the boundary that matters is *between* workspaces and *toward the control plane* — not within a single workspace.

## Workspace isolation

- Each workspace runs on **its own isolated Docker network** (`cove-ws-net-<id>`).
- Egress is firewalled to **WAN-only by default**. The Docker-internal range (`172.16.0.0/12`) and cloud-metadata range (`169.254.0.0/16`) are **always blocked**, so a workspace can never reach the Cove backend, the socket proxies, Traefik, the host metadata service, or other workspaces. See [Networking](networking.md).
- Containers drop all Linux capabilities (re-adding only a minimal set) and run with `no-new-privileges` unless the workspace explicitly requested sudo and the admin hasn't force-disabled it. See [Workspaces → hardening](workspaces.md#sudo--container-hardening).

## Stream authentication

- Workspace streams are **never exposed unauthenticated**. Traefik's ForwardAuth calls back into Cove (`/api/auth/forward`) to authorize every stream request against your session, and that endpoint rejects any request whose `Host` isn't the internal authority, so it can't be probed from outside.
- In **subdomain-isolation** mode, the SPA session cookie is host-only and never reaches a workspace origin; streams use a separate, short-lived, single-workspace `cove_stream` token, handed off once via a one-time bootstrap token in the URL. A hostile workspace can at most obtain a credential for the very workspace its owner already controls. See [Deployment](deployment.md#per-workspace-subdomain-isolation).

## Credentials & secrets

- Passwords are hashed with **bcrypt**. Sessions use short-lived signed JWTs in **httpOnly** cookies; logout and password changes revoke all outstanding tokens.
- Per-user secrets — **SSH private keys, Tailscale auth keys, and Gluetun configs/keys** — are encrypted at rest with a Fernet key derived from the app signing secret before being written to the database. API responses mask them (presence booleans only).
- Optionally, the **entire SQLite database** can be encrypted at rest with SQLCipher via `COVE_DB_ENCRYPTION_KEY` (see [Configuration](configuration.md#at-rest-database-encryption)).
- OIDC ID tokens are verified with the signing algorithm pinned to an asymmetric allowlist (no `HS256`/`none`), and audience/issuer/nonce are all checked.

## Rate limiting & audit

- Login is rate-limited both in the application (per IP) and at the Traefik edge.
- Mutating cross-origin cookie-auth requests are rejected (CSRF protection).
- Security-relevant actions are recorded to an **audit log** (see [Administration → Audit log](administration.md#audit-log)).

## Operator responsibilities

- Always serve **HTTPS** and set **`COVE_COOKIE_SECURE=true`** when reachable beyond `localhost` — otherwise the session cookie travels in cleartext.
- Keep the signing secret (`./data/secret.key`) and any DB-encryption key safe and backed up; losing the DB-encryption key means losing the database.
- Restrict who can reach the host and the Docker daemon.
</content>
