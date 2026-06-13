# Cove Documentation

Full documentation for installing, configuring, and using **Cove** — a
self-hosted, Kasm-style VDI that runs LinuxServer.io desktop and browser
containers and streams them to the browser via Selkies.

If you're new here, start with **[Installation](installation.md)**, then skim the
**[User guide](user-guide.md)**.

## Contents

### Getting started
- **[Installation](installation.md)** — requirements, quick start, day-to-day operations, updating, and resetting.
- **[Configuration](configuration.md)** — the complete `.env` / environment-variable reference, the Compose files, storage layout, DNS pinning, and at-rest DB encryption.
- **[Deployment & HTTPS](deployment.md)** — production HTTPS with Let's Encrypt (TLS-ALPN and DNS-01), wildcard certificates, and per-workspace subdomain isolation.

### Using Cove
- **[User guide](user-guide.md)** — navigation, the dashboard, launching desktops, the "open a website" browser flow, in-stream controls, the file browser, and per-user preferences.
- **[Workspaces](workspaces.md)** — workspace types, every launch option, persistent vs. ephemeral storage, per-workspace apps, SSH-key injection, hardening, and the full workspace lifecycle.
- **[Networking & routing](networking.md)** — the per-workspace egress policy, LAN access, custom DNS, Tailscale routing, and Gluetun VPN routing.

### Operating Cove
- **[Authentication](authentication.md)** — local accounts, sessions and tokens, OIDC/Authentik SSO, and OIDC-only mode.
- **[Administration](administration.md)** — the admin UI: settings, user management, the image catalog, live sessions, and the audit log.
- **[Security model](security.md)** — how Cove isolates workspaces, protects credentials, and what trust assumptions it makes.
- **[API reference](api-reference.md)** — every HTTP endpoint, the auth model, and the SPA routes.
- **[Troubleshooting](troubleshooting.md)** — common symptoms and fixes.

## See also

- **[../ARCH.md](../ARCH.md)** — the architecture deep-dive (runtime topology, auth/stream flows, the data model).
- **[../README.md](../README.md)** — the project overview and feature list.

> **Conventions.** Configuration set through environment variables (the `.env`
> file) requires a restart to take effect. Runtime settings under **Admin →
> Settings** are stored in the database and apply to newly started workspaces
> without a restart — see [Configuration](configuration.md#runtime-settings-admin--settings).
</content>
