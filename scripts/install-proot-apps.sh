#!/bin/bash
# Injected into LinuxServer desktop workspaces (/custom-cont-init.d) to install
# proot-apps listed in $PROOT_APPS (space/comma separated), as the desktop user.
# Best-effort: no-ops if proot-apps isn't present in the image.
# See https://github.com/linuxserver/proot-apps
set -u

apps="${PROOT_APPS:-}"
apps="${apps//,/ }"
[ -z "${apps// /}" ] && exit 0

# Install in the BACKGROUND. custom-cont-init.d runs *before* the desktop
# services start, so a long/large app list here would block the GUI from ever
# becoming ready — the workspace then overruns the launch deadline and is marked
# "error" (and a dozen+ big apps can take far longer than that). Backgrounding
# lets the desktop come up promptly; apps appear in the menu as each finishes.
# Progress is logged to ${log} in the persistent home for debugging.
log=/config/.cove-proot-apps.log

(
  # LinuxServer Selkies-based images (kali-linux, webtop, …) ship the proot-apps
  # binary at /proot-apps (with a per-user copy in ~/.local/bin), neither of which
  # is on the minimal PATH custom-cont-init.d runs with — without this,
  # `command -v proot-apps` fails and we silently skip every app.
  export PATH="/proot-apps:/config/.local/bin:${PATH}"

  if ! command -v proot-apps >/dev/null 2>&1; then
    echo "[cove] proot-apps not available in this image; skipping: $apps"
    exit 0
  fi

  # Per-app download cap. proot-apps' downloader has no timeout, so a throttled /
  # stalled ghcr.io blob pull would otherwise hang this background job forever.
  timeout_cmd=""
  command -v timeout >/dev/null 2>&1 && timeout_cmd="timeout 600"

  for app in $apps; do
    # /config is persistent, so skip apps already installed (proot-apps extracts
    # to this path). Avoids re-downloading ~0.5GB per app on every boot — which
    # both slows recreates and hammers ghcr.io into rate-limiting us.
    if [ -d "/config/proot-apps/ghcr.io_linuxserver_proot-apps_${app}" ]; then
      echo "[cove] proot-apps: ${app} already installed, skipping"
      continue
    fi
    echo "[cove] proot-apps install ${app}"
    HOME=/config ${timeout_cmd} s6-setuidgid abc proot-apps install "${app}" \
      || echo "[cove] proot-apps: failed/timed out installing ${app}"
  done
  echo "[cove] proot-apps: all installs finished"
) >>"${log}" 2>&1 &

echo "[cove] proot-apps: installing in background (${apps}); log: ${log}"
exit 0
