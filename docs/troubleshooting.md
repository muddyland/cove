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
| **GPU workspace stutters / GPU errors** | Cove auto-detects the render-node group per host, so the classic GID mismatch is handled — but confirm **Wayland streaming** is on (required for HW encode), the host GPU isn't oversubscribed by several concurrent GPU workspaces, and the encoder is engaged on the host (`vainfo`, `radeontop`/`intel_gpu_top`). A workspace that errors with *"no render node…"* has GPU on but no usable device — turn GPU off or fix the render node. On a low-power shared iGPU, GPU off can be smoother. See [Workspaces → GPU acceleration](workspaces.md#gpu-acceleration). |
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

## Inspecting a workspace directly

```bash
docker ps --filter name=cove-ws-          # running workspace containers
docker logs cove-ws-<id>                  # a workspace's container logs
docker network ls --filter name=cove      # cove + per-workspace networks
```

In-container install logs (inside the workspace's `/config`):
`/config/.cove-proot-apps.log` and `/config/.cove-appimages.log`.

## Full reset

```bash
docker compose down
sudo rm -rf ./data                        # DB, secret key, default homes
sudo rm -rf /var/lib/cove/workspaces      # persistent homes (if used)
docker compose up --build -d
```

This returns Cove to the first-run setup screen. See
[Installation → Resetting](installation.md#resetting).
</content>
