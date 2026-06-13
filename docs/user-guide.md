# User guide

This is the day-to-day guide to using Cove as a regular user. For the full set of
launch options and the workspace lifecycle, see [Workspaces](workspaces.md).

## Navigation

After signing in you're in the SPA under `/app`. The top navigation has:

- **Dashboard** — your workspaces, plus the launch buttons.
- **Files** — browse your workspace storage.
- **Preferences** — password, SSH key, Tailscale, Gluetun.
- **Admin** (admins only) — Users, Sessions, Images, Audit, Settings. See [Administration](administration.md).

## The dashboard

The dashboard ("Workspace Grid") lists **your own** workspaces, split into
**Active** and **Offline**. Each running card shows:

- A live **CPU** and **memory** readout for the container.
- The **status** (booting / provisioning / running / error).
- For tailnet-routed workspaces, the **Tailscale IP** (copyable).
- Actions: **Connect**, **Logs**, **Halt**, and **Purge** (delete).

Two launch buttons sit at the top:

- **Deploy Node** — launch a desktop or browser workspace (opens the Launch modal).
- **Open Website** — the quick "open a URL in a browser" flow.

## Launching a desktop

1. Click **Deploy Node**.
2. Give it a **name** and pick an **image** (e.g. a Webtop XFCE/KDE/MATE variant, or Kali).
3. Optionally expand the extra options (apps, networking, sudo, SSH) — see [Workspaces → Launch options](workspaces.md#launch-options).
4. Click **Launch**. Cove creates the container and takes you to the workspace view, which shows a **Booting / Provisioning** screen until the desktop is ready, then streams it into the page.

The first launch of an image pulls it from `lscr.io` and can take a few minutes.

## Opening a website (browser workspaces)

Browser images (Chromium, Brave, Firefox, Edge) can boot straight to one or more
URLs — a lightweight way to deliver a web app:

1. Click **Open Website** (or choose a browser image in **Deploy Node**).
2. Enter one URL per line — **up to 6**; each opens in its own tab. Multiple tabs open full-screen with a tab bar.
3. Pick the **browser**.
4. Options for a single URL:
   - **Kiosk mode** — full-screen with no browser chrome.
   - **Dark mode** — forces the page into dark mode (kiosk only).
   - **Allow right-click / refresh menu** — keeps a minimal menu instead of a hard kiosk lock.
5. Optionally tick **Ephemeral** (no saved data — cookies/history/downloads wiped on halt) or **Route through Tailscale**.
6. Click **Launch**.

## In-stream controls

While viewing a running workspace, a top bar provides:

- **Quick-switch menu** — a dropdown next to the workspace name listing all your workspaces (running first). Jump between them, and **boot a stopped one in place** without returning to the dashboard.
- **Fullscreen** (`FULL` / `WINDOW`) — expand the stream to fill the window.
- **CRT** — a retro scanline/flicker overlay (cosmetic; per-user toggle).
- **Logs** — opens diagnostics: container logs and (for tailnet workspaces) `tailscale status`.
- **HALT** — stop and remove the container (persistent data is kept).
- **APP / install** — install *this* workspace as its own PWA (its own icon and window). Handy for a single-purpose browser workspace.
- **Connection indicators** — a lock icon when routed through a VPN (Gluetun), a network icon when routed through Tailscale (with the exit node in the tooltip).

## The file browser

**Files** lets you browse, upload, download, and delete files within **your own**
storage area (`<storage>/<your-username>/…`, which holds your workspaces' `/config`
homes). Access is confined to your directory — path traversal is rejected — and a
single upload may be up to `COVE_MAX_UPLOAD_MB` (default **1024 MiB**).

## Preferences

Manage your own account and routing under **Preferences**:

- **Password** — change it (local accounts only; hidden for SSO accounts). Minimum 8 characters; changing it signs out your other sessions.
- **SSH key** — generate a fresh **Ed25519** keypair, or upload your own private key (ed25519/rsa/ecdsa/dsa; **unencrypted** keys only — passphrase-protected keys are rejected). The public key and fingerprint are shown; the private key is encrypted at rest and never returned. When set, it's injected into each workspace's `~/.ssh` at launch (toggle per workspace with **Inject SSH key**), so in-container `git`/`ssh` works out of the box.
- **Tailscale** — enable it and store your **auth key** (a preauth key, encrypted at rest) and an optional **login/control server** (e.g. a Headscale `https://` URL). Per-launch options (exit node, accept routes/DNS) are chosen when launching a workspace.
- **Gluetun (VPN)** — enable it, choose **OpenVPN** or **WireGuard**, and upload your VPN **config file** (encrypted at rest, up to 128 KiB). You can optionally override the WireGuard private key or OpenVPN username/password as separate secrets.

See [Networking & routing](networking.md) for how Tailscale and Gluetun apply to
a workspace's traffic.
</content>
