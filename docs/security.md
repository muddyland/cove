# Security model

A summary of how Cove isolates workspaces, protects credentials, and what it
assumes about its environment. For the deep architecture, see
**[../ARCH.md](../ARCH.md)**.

## Trust boundaries

- **The host is trusted.** Cove manages containers through a Docker socket proxy; anyone with host or Docker-daemon access has full control. Run Cove on a host you trust.
- **The Docker socket is never exposed directly.** Two filtered proxies sit in front of it: a **write-capable** proxy used only by the Cove backend (to create/stop containers), and a **read-only** proxy used only by Traefik (for service discovery, so a Traefik compromise can't create containers). Be clear about what "filtered" means for the write side: it can create privileged containers and exec into any container, so **a compromise of the Cove backend is a compromise of the host**. The read-only proxy can still read every container's environment.
- **Docker-in-Docker is host root.** The per-workspace nested daemon runs privileged; the feature is off by default, gated per account, and documented as equivalent to a root shell on the host. On a remote zone the agent refuses privileged containers unless that zone's host opted in (`COVE_AGENT_ALLOW_DIND`, set by re-running the installer), and then accepts only the DinD sidecar's exact shape. See [Deployment](deployment.md#docker-in-docker-development-inside-a-workspace).
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
- The agent refuses any Docker API path that isn't already in its final form (dot or empty segments, backslashes, encoded slashes or dots), so the path its policy checks is always the path the daemon serves.

## Thin clients

- A thin client **only dials out**: its agent long-polls Cove over HTTPS with a random device token, stored root-only on the device and only as a sha256 in Cove. Enrolment codes are single-use and short-lived. See [Thin clients → Security model](thin-clients.md#7-security-model).
- **Sessions belong to the device.** The kiosk browser is bound with a one-time ticket and an httpOnly cookie (the device token never enters the browser). Every token minted there, workspace stream cookies included, carries the device and its sign-in time, and dies on that device's sign-out, idle timeout or **Longest session** cap. Signing a kiosk out never signs the user out of their other browsers.
- **It fails closed.** A browser whose binding is revoked, expired or whose device is gone can't sign in at all, and at the cap Cove restarts the browser on a wiped profile, so the next person can't inherit an identity provider's cookie.
- **Cove has root on its thin clients**, by design: their agent updates itself from Cove. The agent never acts as root inside the kiosk user's home, so a browser compromise can't use it as a path to root. Install USBs require an `https://` Cove address.

## Integration tokens (Home Assistant)

- An integration token works on `/api/integration/v1` **and nowhere else**: it is not a sign-in and can't open workspaces, files or admin endpoints. Only its sha256 is stored.
- **Read** sees counters and who is signed in on which thin client; **Control** also sees screenshots and can command devices. Screenshots, disruptive commands, and token creation and revocation are audited.
- A token acts with its creator's admin authority and is revoked when that account is deleted or demoted. See [Home Assistant → Tokens and security](home-assistant.md#tokens-and-security).

## Rate limiting & audit

- Login is rate-limited both in the application (per IP) and at the Traefik edge.
- Mutating cross-origin cookie-auth requests are rejected (CSRF protection).
- Admins can **limit an account to certain zones**; the limit is enforced server-side wherever a workspace is placed, run or given files, and a limited account is never told the names or settings of zones it can't use.
- Security-relevant actions are recorded to an **audit log** (see [Administration → Audit log](administration.md#audit-log)).

## Operator responsibilities

- Always serve **HTTPS** and set **`COVE_COOKIE_SECURE=true`** when reachable beyond `localhost` — otherwise the session cookie travels in cleartext.
- Keep the signing secret (`./data/secret.key`) and any DB-encryption key safe and backed up; losing the DB-encryption key means losing the database.
- Restrict who can reach the host and the Docker daemon.
