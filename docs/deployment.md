# Deployment & HTTPS

This page covers running Cove for real: HTTPS with Let's Encrypt, the choice
between the TLS-ALPN and DNS-01 challenges, wildcard certificates, and
per-workspace subdomain isolation.

Before exposing Cove beyond `localhost`, always:

- Serve **HTTPS**.
- Set **`COVE_COOKIE_SECURE=true`** (the production Compose file does this for you).

## TLS-ALPN challenge (ports 80/443 reachable)

The simplest production setup. Requires inbound `:80` and `:443` from the
internet to the host.

```ini
# .env
COVE_DOMAIN=cove.example.com
COVE_ACME_EMAIL=you@example.com
COVE_COOKIE_SECURE=true
```

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

This adds the `:443` entrypoint, an HTTP→HTTPS redirect, the Let's Encrypt
resolver, HSTS, and binds the app router to your domain. The certificate is
issued on the first request.

## DNS-01 challenge (closed ports, or wildcard certs)

Use DNS-01 when `:80`/`:443` aren't reachable from the internet, or when you need
a **wildcard** certificate (required for [subdomain isolation](#per-workspace-subdomain-isolation)).

```ini
# .env (in addition to COVE_DOMAIN / COVE_ACME_EMAIL / COVE_COOKIE_SECURE)
COVE_ACME_DNS_PROVIDER=cloudflare
COVE_ACME_DNS_RESOLVERS=1.1.1.1:53,8.8.8.8:53
CF_DNS_API_TOKEN=your-scoped-cloudflare-token   # provider-specific
```

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.dns.yml up -d
```

Credential variable names depend on your provider — Traefik reads them straight
from the environment. See the
[Traefik DNS providers list](https://doc.traefik.io/traefik/https/acme/#providers).

## LAN / self-signed HTTPS (no public domain)

For a private network with no public domain or Let's Encrypt access. **HTTPS is
not optional here** — LinuxServer/Selkies workspace streams require a browser
["secure context"](https://developer.mozilla.org/docs/Web/Security/Secure_Contexts)
and refuse to start over plain HTTP with *"This application requires a secure
connection (HTTPS)."* Serving over HTTP gets you the control UI but every stream
fails.

Generate a self-signed certificate for the host's LAN address(es) — pass the IP
and/or any hostnames you'll browse to; they become the certificate's SANs:

```bash
scripts/gen-lan-cert.sh 192.168.0.10 myhost.local
```

This writes `certs/cove.crt`, `certs/cove.key`, and `certs/dynamic.yml` (the
Traefik default-cert config). Then set the origin you reach the box at:

```ini
# .env
COVE_COOKIE_SECURE=true
COVE_APP_ORIGIN=https://192.168.0.10
```

```bash
docker compose -f docker-compose.yml -f docker-compose.lan-tls.yml up -d
```

The override adds the `:443` entrypoint, an HTTP→HTTPS redirect, and serves the
self-signed cert as the default certificate.

**Keep `COVE_WORKSPACE_DOMAIN` unset** (the default subpath mode): every stream
is then same-origin under `/workspace/<id>/`, so this **one** certificate covers
the app *and* all streams. Subdomain isolation would instead need a *wildcard*
cert, which self-signing per workspace can't practically provide.

Browsers show a one-time "not trusted" warning for a self-signed cert — click
through once. To silence it, import `certs/cove.crt` into the trust store of the
devices you connect from (or generate the cert with a tool like
[`mkcert`](https://github.com/FiloSottile/mkcert), whose local CA you install on
each device). Because it's subpath mode, you only trust the one origin — the
workspace stream inherits it.

## Per-workspace subdomain isolation

By default, workspaces stream at a **subpath** (`/workspace/<id>/`) on the same
origin as the control UI. For stronger isolation you can give each workspace its
**own origin**, so a workspace can never reach the SPA's session token:

```ini
# .env
COVE_WORKSPACE_DOMAIN=cove.example.com   # workspaces at <public_id>.cove.example.com
```

How it protects you:

- The SPA session cookie is **host-only** and is never sent to workspace origins.
- Each stream is authorized by a separate, short-lived **per-workspace stream
  token** (a host-only `cove_stream` cookie scoped to that one workspace origin).
- A hostile workspace can therefore only ever obtain a credential for the exact
  workspace the user already owns — not the session or admin cookie.

This mode requires a **wildcard DNS record** (`*.cove.example.com`) and a
**wildcard TLS certificate**, so deploy it with the DNS-01 override and uncomment
the wildcard SAN labels in `docker-compose.dns.yml`:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.dns.yml up -d
```

Leave `COVE_WORKSPACE_DOMAIN` unset to keep the simpler subpath routing (no
wildcard needed). The trade-offs are discussed further in
[Security model](security.md).

## Behind another reverse proxy

Cove's own Traefik terminates TLS and performs ForwardAuth on every stream. If
you must place Cove behind an upstream proxy, forward to Traefik's entrypoint and
preserve the `Host` header and `X-Forwarded-*` headers — the backend derives the
real client IP from the rightmost `X-Forwarded-For` hop (the one Traefik appends),
which is used for rate limiting and the audit log.

## After deploying

- Confirm health: `curl https://cove.example.com/api/health` should return `200`.
- Configure SSO if desired — see [Authentication](authentication.md).
- Review [Administration → Settings](administration.md#settings) to set resource
  limits, max runtime, and (if needed) LAN access.
- If the catalog is empty, run **Admin → Images → Sync LinuxServer**.

If certificates don't issue or workspaces 404, see
[Troubleshooting](troubleshooting.md).
</content>
