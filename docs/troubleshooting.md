# Troubleshooting

Common symptoms and fixes. For backend logs: `docker compose logs -f cove`.

## Quick reference

| Symptom | Likely cause / fix |
|---|---|
| **Catalog is empty** | The first-run auto-seed couldn't reach the LinuxServer API. Run **Admin → Images → Sync LinuxServer** (needs outbound internet). |
| **Login works but nothing happens / you stay logged out** | Serving plain HTTP with `COVE_COOKIE_SECURE=true`. Set `COVE_COOKIE_SECURE=false` for `http://localhost`, or serve HTTPS and keep it `true`. |
| **Workspace stuck on "Booting / Provisioning"** | First pull of a large image, or a long package/proot/AppImage install (those run in the background and keep the workspace provisioning). Watch `docker compose logs -f cove`, `docker images`, and the in-container logs. |
| **`502` on a workspace stream** | The container is still booting or unhealthy. Wait, or check `docker logs cove-ws-<id>`. |
| **Stream shows "This application requires a secure connection (HTTPS)"** | The Selkies stream needs a browser secure context — you're serving Cove over plain HTTP. Serve HTTPS. No public domain? Use the [LAN self-signed setup](deployment.md#lan--self-signed-https-no-public-domain). |
| **"Temporary failure in name resolution" in cove logs** | The cove container has no working DNS (Docker stripped a loopback resolver). Set `COVE_DNS_PRIMARY`/`COVE_DNS_SECONDARY` — to your LAN DNS if internal services only resolve there. |
| **OIDC discovery fails** | Same DNS issue as above, or the issuer URL is wrong / unreachable from the container. Verify `COVE_OIDC_ISSUER` and DNS. |
| **404 on everything + Traefik logs `client version 1.24 is too old`** | Newer Docker Engine rejects the API version Traefik probes with. See [below](#docker-daemon-client-version-124-is-too-old). |
| **TLS certificate won't issue** | TLS-ALPN needs inbound `:80`/`:443`; if those are closed, switch to DNS-01. Check `COVE_DOMAIN`/`COVE_ACME_EMAIL` and Traefik logs. |
| **Tailscale/Gluetun workspace won't start** | The host needs `/dev/net/tun`. Confirm the user has a valid auth key / uploaded VPN config in Preferences. Only one active Gluetun workspace per user is allowed. |
| **GPU workspace stutters / GPU errors** | Cove auto-detects the render-node group per host, so the classic GID mismatch is handled — but confirm **Wayland streaming** is on (required for HW encode), the host GPU isn't oversubscribed by several concurrent GPU workspaces, and the encoder is engaged on the host (`vainfo`, `radeontop`/`intel_gpu_top`). A workspace that errors with *"no render node…"* has GPU on but no usable device — turn GPU off or fix the render node. On a low-power shared iGPU, GPU off can be smoother. See [Workspaces → GPU acceleration](workspaces.md#gpu-acceleration). Note that Wayland streaming is also what breaks cursor shapes ([below](#mouse-cursor-never-changes-shape)) — the two cannot both be satisfied. |
| **Stream freezes every few seconds over the internet, then catches up in a burst; a refresh fixes it briefly** | An upstream reverse proxy (e.g. Nginx Proxy Manager) buffering the WebSocket stream on the WAN leg. Turn off proxy buffering and raise its timeouts for the workspace hosts. See [below](#stream-freezes-over-the-internet-behind-a-reverse-proxy). |
| **An app install/update says "interrupted"** | Its task died with the container — a halt, a restart, or an out-of-memory kill mid-download. Nothing is half-installed (an AppImage update only swaps in after a clean extract), so start it again from **Actions → Apps**. |
| **An app task won't start ("workspace is busy", 429)** | A workspace runs one app task at a time, at most three queued, and Cove caps how many driver calls it makes at once. Wait for the running task (tasks menu) and retry. |
| **A browser shows its own welcome/setup screen instead of the link** | The browser's first-run experience on a fresh profile (Vivaldi's setup window, Opera's welcome tabs). Not a Cove or image setting. It happens once per profile, so a persistent workspace only needs it dismissed once; ephemeral ones hit it every launch — use Chromium or Helium there. See [below](#a-browser-opens-its-own-welcome-screen-instead-of-the-link). |
| **Mouse cursor never changes shape** | Not a theme problem. Selkies sends cursor shapes as stream metadata and skips that entirely on Wayland, which Cove uses by default. Turn **Wayland streaming** off for that workspace. See [below](#mouse-cursor-never-changes-shape). |
| **Can't reach a LAN host from a workspace** | LAN access needs both the admin master toggle + allowed subnets **and** the per-workspace opt-in. Docker-internal/metadata ranges are always blocked. "Open a website" to a LAN host works via the per-URL `/32` exception. |
| **Locked out after enabling OIDC-only** | A broken OIDC config disables OIDC-only automatically. To force recovery, set `COVE_OIDC_ONLY=false` on the server and restart. |
| **Upload rejected with `413`** | The file exceeds `COVE_MAX_UPLOAD_MB` (default 1024 MiB). Raise it and restart, or split the upload. |

## Docker daemon `client version 1.24 is too old`

Recent Docker Engine raised its minimum API version, which breaks Traefik's
Docker provider (it probes with `/v1.24/...`), so no routers are discovered and
every request 404s. Re-enable backward compatibility on the **host** daemon:

```bash
sudo mkdir -p /etc/systemd/system/docker.service.d
printf '[Service]\nEnvironment=DOCKER_MIN_API_VERSION=1.24\n' \
  | sudo tee /etc/systemd/system/docker.service.d/api-compat.conf
sudo systemctl daemon-reload
sudo systemctl restart docker      # briefly restarts all containers
```

After the daemon comes back, the Cove stack auto-restarts (`restart:
unless-stopped`), Traefik discovers the routers, and ACME issues the certificate
on the first request.

## Mouse cursor never changes shape

**Symptom.** Inside a workspace the mouse pointer stays a plain arrow everywhere:
no I-beam over text, no resize arrows on window edges, no pointing finger over
links. Everything else works, and nothing logs an error — which makes it look
like a missing cursor theme even though the themes and the `XCURSOR_*` variables
are fine.

**Cause.** The pointer is never drawn into the video. Selkies sends the cursor
*shape* to the browser as metadata — on X11 it subscribes to
`XFixesDisplayCursorNotify`, PNG-encodes each new cursor, and sends a
`cursor,{...}` message that the browser applies as a CSS `url(...)` cursor. When
the stream runs on **Wayland** (`PIXELFLUX_WAYLAND=true`) that monitor is skipped
outright — selkies logs *"Wayland mode: Cursor monitor disabled (handled by
compositor callback)."* and returns. Its Wayland replacement is a capture-module
cursor callback that may never fire under KWin, and there is no fallback to
XFIXES. The result is a permanently static arrow. This is upstream Selkies
behaviour, not a Cove bug.

Cove's per-workspace **Wayland streaming** toggle defaults to **on**
(`pixelflux_wayland: bool = True`), so workspaces are affected by default.

**Fix.** Turn **Wayland streaming** off for that workspace (Access options at
launch, or Edit on an existing one). That forces the X11/Xvfb fallback, where the
XFIXES cursor monitor runs and cursor shapes work again.

**Trade-off.** GPU hardware encode *requires* Wayland streaming — enabling GPU
with Wayland off is rejected. So a workspace can have hardware encode **or**
working cursor shapes, not both. Pick per workspace. See
[Workspaces → GPU acceleration](workspaces.md#gpu-acceleration).

To confirm which mode a workspace is in:

```bash
# inside the workspace (a terminal in the desktop)
echo "$PIXELFLUX_WAYLAND"                  # true = Wayland, so cursor shapes are off

# on the host — selkies logs to the container's stdout
docker logs cove-ws-<id> | grep -i cursor
# "Cursor monitor disabled"     → Wayland: static arrow
# "watching for cursor changes" → X11: shapes work
```

## Stream freezes over the internet (behind a reverse proxy)

**Symptom.** Streaming a workspace from outside your network, the picture freezes
for a moment every few seconds, even while you're idle. When it comes back, a
blinking text cursor flashes rapidly as delayed frames arrive all at once, and
keystrokes typed during the freeze still land. A page refresh makes it smooth
again, but only for a while. On the LAN the same workspace is fine. Opening the
browser's developer console on the stream shows repeated
`[websockets] Connection closed` / `reloading page to reconnect` messages, and
sometimes `Critical decode error` or `FATAL DECODER ERROR`.

**Cause.** The stream is one long-lived WebSocket carrying live video. Selkies
applies no send-side flow control to it: every encoded frame is pushed straight
into the connection's write buffer whether or not the link is keeping up. A
reverse proxy in front of Cove on the WAN side, such as **Nginx Proxy Manager**
with its defaults, buffers that traffic like an ordinary web response. Whenever
the WAN leg momentarily can't keep up, frames pile up behind the proxy and are
delivered late in a burst. That's the freeze and the fast-forward.

If the browser's video decoder hits an error during one of those stalls, the
Selkies client first restarts video (another freeze). On a second error it closes
the connection and reloads the page, which on a struggling link repeats every
20–30 seconds. After three such crashes the client silently switches that browser
to the JPEG encoder, which it remembers in the browser's local storage.

**Fix.** Configure the upstream proxy for streaming on every host that serves
Cove workspaces, including the `*.<workspace domain>` wildcard host in subdomain
mode. In Nginx Proxy Manager, open the proxy host:

1. **Details:** enable **Websockets Support**.
2. **Advanced → Custom Nginx Configuration:**

   ```nginx
   proxy_buffering off;
   proxy_request_buffering off;
   proxy_read_timeout 3600s;
   proxy_send_timeout 3600s;
   tcp_nodelay on;
   ```

For another nginx-based proxy, add the same directives to the `location` that
proxies to Cove's Traefik. If a CDN or tunnel sits in front of the proxy (for
example Cloudflare's orange-cloud proxy), try bypassing it too, since it adds its
own buffering and WebSocket limits.

If a browser already fell back to JPEG, pick **x264enc** again in the Selkies
sidebar's video settings once the stream is stable.

**Confirm it's the proxy.** Open the same workspace for a few minutes over a path
that skips it: on the LAN, or over Tailscale straight to Cove. Smooth there and
freezing through the proxy means it's the WAN path. If it still freezes on the
LAN, the cause is elsewhere; capture the `[websockets] Connection closed` line
(it includes a close code) and the desktop log (**Logs → Desktop** in the
workspace menu) from right after a freeze.

If the proxy is configured correctly and it still stalls, the link itself can't
carry the stream. Lower the frame rate or raise the H.264 CRF in the Selkies
sidebar to cut bitrate.

## A browser opens its own welcome screen instead of the link

**Symptom.** A browser workspace (or an "Open in Cove" link) starts, but the page
you asked for is behind the browser's own first-run screen. **Vivaldi** shows a
modal *"Let's get you set up"* window — the requested URL is loaded behind it, so
it looks like the link was ignored. **Opera** opens its welcome/onboarding tabs
alongside the link. **Chromium** and **Helium** go straight to the URL.

**Why.** This is the browser's own first-run experience, and it is tied to the
*profile*, not to Cove. It fires once per fresh profile:

- A **persistent** browser workspace shows it on its first launch only. Close the
  welcome window/tabs once and it never comes back — the profile in `/config`
  remembers.
- An **ephemeral** workspace (the "Open in Cove" extension, or any workspace set
  to discard its home) starts from an empty profile every time, so it shows every
  time. If that flow matters more than the browser, use Chromium or Helium for
  it — neither has a first-run screen.

**There is no setting for it**, in Cove or in the image. LinuxServer's wrapper
already passes `--no-first-run` to Vivaldi and exposes no environment variable
for this; the container only forwards `VIVALDI_CLI` / `OPERA_CLI` to the browser.
For the record, these were tested against the real images and did *not* suppress
Vivaldi's wizard:

- `--no-first-run` (already passed by the image) and a `First Run` sentinel file
  in the user-data directory;
- a managed enterprise policy in `/etc/vivaldi/policies/managed/`
  (`PromotionalTabsEnabled`, `DefaultBrowserSettingEnabled`, `BrowserSignin`);
- seeding a fresh profile's `Preferences` with
  `vivaldi.startup.has_seen_welcome_page`, `first_seen_version` and
  `vivaldi.welcome.read_pages`.

Vivaldi decides this in its browser process, not from a pref Cove can pre-set.
Kiosk and full-screen still apply to the window underneath, so on a persistent
workspace the only cost is dismissing the screen once.

## Inspecting a workspace directly

```bash
docker ps --filter name=cove-ws-          # running workspace containers
docker logs cove-ws-<id>                  # a workspace's container logs
docker network ls --filter name=cove      # cove + per-workspace networks
```

In-container install logs (inside the workspace's `/config`):
`/config/.cove-proot-apps.log` and `/config/.cove-appimages.log` hold what each
boot installed. Tasks started from the Apps dialog log only to the navbar's tasks
menu — their state lives in `/tmp/cove-apps-<uid>/` inside the running container,
so it goes when the workspace halts.

## Full reset

```bash
docker compose down
sudo rm -rf ./data                        # DB, secret key, default homes
sudo rm -rf /var/lib/cove/workspaces      # persistent homes (if used)
docker compose up --build -d
```

This returns Cove to the first-run setup screen. See
[Installation → Resetting](installation.md#resetting).
