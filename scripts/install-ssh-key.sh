#!/bin/bash
# Injected into LinuxServer workspaces (/custom-cont-init.d) to install the
# owner's account SSH key into ~/.ssh, so in-container git/ssh "just works".
#
# The key files are bind-mounted read-only at /cove/ssh-key (root-owned, named
# id_<type> and id_<type>.pub). We copy them into the desktop user's /config/.ssh
# with strict permissions and correct ownership (init scripts run as root; the
# 'abc' desktop user already exists by the time custom-cont-init.d runs).
#
# Best-effort: never fails container init.
set -u

SRC=/cove/ssh-key
HOME_DIR=/config
SSH_DIR="${HOME_DIR}/.ssh"
USER_NAME=abc

[ -d "${SRC}" ] || exit 0

mkdir -p "${SSH_DIR}"
chmod 700 "${SSH_DIR}"

for f in "${SRC}"/*; do
  [ -e "$f" ] || continue
  base="$(basename "$f")"
  dest="${SSH_DIR}/${base}"
  cp -f "$f" "${dest}" || continue
  case "${base}" in
    *.pub) chmod 644 "${dest}" ;;
    *)     chmod 600 "${dest}" ;;
  esac
  echo "[cove] ssh-key: installed ${base}"
done

# Hand the whole .ssh dir to the desktop user (uid/gid resolve via PUID/PGID).
chown -R "${USER_NAME}:${USER_NAME}" "${SSH_DIR}" 2>/dev/null || true

exit 0
