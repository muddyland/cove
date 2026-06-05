#!/bin/bash
# Injected into LinuxServer desktop workspaces (/custom-cont-init.d) to install
# proot-apps listed in $PROOT_APPS (space/comma separated), as the desktop user.
# Best-effort: no-ops if proot-apps isn't present in the image.
# See https://github.com/linuxserver/proot-apps
set -u

apps="${PROOT_APPS:-}"
apps="${apps//,/ }"
[ -z "${apps// /}" ] && exit 0

if ! command -v proot-apps >/dev/null 2>&1; then
  echo "[cove] proot-apps not available in this image; skipping: $apps"
  exit 0
fi

for app in $apps; do
  echo "[cove] proot-apps install ${app}"
  HOME=/config s6-setuidgid abc proot-apps install "${app}" \
    || echo "[cove] proot-apps: failed to install ${app}"
done
