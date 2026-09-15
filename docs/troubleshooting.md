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

## Inspecting a workspace directly

```bash
docker ps --filter name=cove-ws-          # running workspace containers
docker logs cove-ws-<id>                  # a workspace's container logs
docker network ls --filter name=cove      # cove + per-workspace networks
```

In-container install logs (inside the workspace's `/config`):
`/config/.cove-proot-apps.log` and `/config/.cove-appimages.log`. proot-apps
tasks started from the Apps dialog log only to the navbar's tasks menu (their
state lives in `/tmp/cove-proot-apps-<uid>/` inside the running container).

## Full reset

```bash
docker compose down
sudo rm -rf ./data                        # DB, secret key, default homes
sudo rm -rf /var/lib/cove/workspaces      # persistent homes (if used)
docker compose up --build -d
```

This returns Cove to the first-run setup screen. See
[Installation → Resetting](installation.md#resetting).
