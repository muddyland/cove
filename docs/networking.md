# Networking & routing

Every workspace runs on its **own isolated Docker network** and has its egress
filtered. This page explains the default policy and the four per-workspace
routing options: LAN access, custom DNS, Tailscale, and Gluetun.

## Egress policy (the default)

Workspaces are **WAN-only by default**. Cove applies an iptables rule set to each
workspace's network namespace at start. In order, the rules:

1. Allow loopback, the embedded Docker DNS resolver (`127.0.0.11`), and established/related traffic.
2. *(Tailscale workspaces only)* Allow everything leaving the `tailscale0` interface — covering the tailnet, peers, exit node, and subnet routes.
3. **Always block** these, for **every** workspace (including Tailscale and LAN-granted ones):
   - `169.254.0.0/16` — link-local and **cloud metadata** (e.g. `169.254.169.254`).
   - `172.16.0.0/12` — the Docker bridge ranges (the Cove backend, the socket proxies, Traefik, and other workspaces).
4. Allow any admin-granted **LAN subnets** and the specific host(s) a `target_url` points at.
5. Block the remaining private ranges: `10.0.0.0/8`, `192.168.0.0/16`, `100.64.0.0/10` (CGNAT).
6. Otherwise allow — i.e. the public internet is reachable.

The net effect: a workspace can reach the internet but **cannot reach the Cove
control plane, the host's metadata service, or other workspaces**. IPv6 is
disabled on the workspace network (the guard only writes IPv4 rules).

## LAN access

Reaching hosts on your local network from a workspace requires **two** switches:

1. **Admin enables it** — the master **LAN access** toggle plus a list of allowed IPv4 subnets, under [Admin → Settings](administration.md#settings).
2. **The workspace opts in** — tick **LAN access** at launch.

Only the admin-listed subnets become reachable; the always-blocked Docker and
metadata ranges stay blocked regardless. The launch checkbox only appears when the
admin has enabled LAN access and configured subnets.

**Exception — "open a LAN website":** a workspace can always reach the specific
host(s) its **target URL** resolves to, added as narrow `/32` rules, even without
the admin LAN toggle. This is what makes "open `http://nas.local`" work out of the
box. Only addresses in the private ranges qualify; Docker-internal and metadata
addresses remain blocked.

## Custom DNS

Tick **Custom DNS** and supply up to six resolver IPs to point a workspace at
specific resolvers (e.g. `1.1.1.1`, `9.9.9.9`) instead of Docker/host DNS. If you
leave the list empty, public defaults are used. Custom DNS is **ignored for
Tailscale workspaces**, because `tailscaled` owns the namespace's `resolv.conf`.

> This is distinct from `COVE_DNS_PRIMARY`/`COVE_DNS_SECONDARY`, which set the
> resolver for the **cove backend container** itself (see
> [Configuration](configuration.md#core--networking)).

## Tailscale routing

Route a workspace's traffic through your tailnet, optionally via an exit node.

1. In **Preferences → Tailscale**, enable Tailscale and store a **preauth key** (encrypted at rest). Optionally set a custom **login/control server** (e.g. Headscale).
2. At launch, tick **Route through Tailscale** and pick per-connection options: **exit node**, **accept routes**, **accept DNS**.

Cove starts a dedicated `tailscale/tailscale` sidecar for that workspace; the
workspace shares the sidecar's network namespace, so its egress leaves via your
tailnet. The egress firewall is applied to the sidecar's namespace **before** the
workspace joins (closing the startup race), while still permitting tailnet/exit-node/subnet traffic. The host needs `/dev/net/tun`. The sidecar and its state
are removed when the workspace is halted.

The sidecar image is pinned by the admin (default `tailscale/tailscale:latest`,
see [Admin → Settings](administration.md#settings)).

## Gluetun VPN routing

Route a workspace's egress through a commercial/self-hosted VPN.

1. In **Preferences → Gluetun**, enable it, choose **OpenVPN** or **WireGuard**, and upload your config file (encrypted at rest, ≤128 KiB). Optionally override the WireGuard private key or OpenVPN credentials as separate secrets.
2. At launch, tick **Route through Gluetun**.

The workspace joins a per-workspace [Gluetun](https://github.com/qdm12/gluetun)
sidecar and inherits its VPN tunnel. Gluetun's own **killswitch firewall** is the
egress control here (Cove doesn't add its own rules, which would fight it); it's
configured to still let Traefik reach the stream port. **Only one active Gluetun
workspace per user** is allowed at a time. Tailscale and Gluetun are mutually
exclusive on a single workspace. The sidecar image is pinned by the admin
(default `qmcgaw/gluetun:latest`).

## Subdomain isolation

Independent of egress, you can isolate each workspace's **inbound** origin so it
can't read the SPA's token — see
[Deployment → Subdomain isolation](deployment.md#per-workspace-subdomain-isolation).
</content>
