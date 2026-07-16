#!/bin/bash
# Injected into a workspace (/custom-cont-init.d) when Docker-in-Docker is enabled.
# Installs the static `docker` CLI so the workspace can talk to its per-workspace
# DinD sidecar (DOCKER_HOST=tcp://127.0.0.1:2375, set by the backend). The daemon
# runs in the sidecar — this only provides the client. Distro-independent (no
# apk/apt divergence). Best-effort: never blocks the desktop from starting.
# Override the pinned client version with COVE_DOCKER_CLI_VERSION if needed.
set -u

# Already have a client (image shipped one, or a previous boot installed it)?
if command -v docker >/dev/null 2>&1; then
  echo "[cove] docker CLI already present; skipping install"
  exit 0
fi

version="${COVE_DOCKER_CLI_VERSION:-27.5.1}"

case "$(uname -m)" in
  x86_64|amd64) arch=x86_64 ;;
  aarch64|arm64) arch=aarch64 ;;
  armv7l) arch=armhf ;;
  *) echo "[cove] docker CLI: unsupported arch $(uname -m); skipping"; exit 0 ;;
esac

url="https://download.docker.com/linux/static/stable/${arch}/docker-${version}.tgz"

# Install in the BACKGROUND: custom-cont-init.d runs before the desktop services
# start, so a slow/stalled CDN pull here would delay the GUI becoming ready (and
# risk the launch deadline). The client lands in ~a second on a normal link and
# is ready by the time a terminal is opened; progress is logged for debugging.
log=/config/.cove-docker-cli.log

(
  timeout_cmd=""
  command -v timeout >/dev/null 2>&1 && timeout_cmd="timeout 300"

  tmp="$(mktemp -d)"
  echo "[cove] docker CLI: fetching ${url}"
  ok=1
  if command -v curl >/dev/null 2>&1; then
    ${timeout_cmd} curl -fsSL "$url" -o "$tmp/docker.tgz" || ok=0
  elif command -v wget >/dev/null 2>&1; then
    ${timeout_cmd} wget -qO "$tmp/docker.tgz" "$url" || ok=0
  else
    echo "[cove] docker CLI: neither curl nor wget available; skipping"
    ok=0
  fi

  if [ "$ok" = 1 ]; then
    # Extract ONLY the client binary (the tarball also ships dockerd etc., which
    # we don't want — the daemon lives in the sidecar).
    if tar -xzf "$tmp/docker.tgz" -C "$tmp" docker/docker 2>/dev/null; then
      install -m 0755 "$tmp/docker/docker" /usr/local/bin/docker \
        && echo "[cove] docker CLI ${version} installed at /usr/local/bin/docker" \
        || echo "[cove] docker CLI: install to /usr/local/bin failed"
    else
      echo "[cove] docker CLI: extract failed"
    fi
  fi
  rm -rf "$tmp"
) >>"${log}" 2>&1 &

echo "[cove] docker CLI: installing in background (${version}/${arch}); log: ${log}"
exit 0
