#!/bin/bash
# Injected into LinuxServer workspaces (/custom-cont-init.d) to make the desktop
# user appear under the owner's Cove username — whoami, the shell prompt (\u),
# and `ls -l` all show it — WITHOUT renaming the image's 'abc' user.
#
# Why an alias instead of a rename: the LinuxServer images hardwire 'abc' BY NAME
# throughout their own s6 services (`s6-setuidgid abc`, `chown abc:abc`,
# `pgrep -u abc`, …), which run after custom-cont-init.d. Renaming abc makes all
# of those lookups fail and the desktop never starts. Instead we add a SECOND
# passwd/group entry that shares abc's UID/GID but carries the Cove username,
# placed BEFORE the abc line — so getpwuid()/getgrgid() (used by whoami, bash's
# \u, ls -l) resolve to the Cove name via files-NSS first-match, while
# getpwnam("abc") still works for the image's services. Kernel-side it remains
# one user (same UID, same /config home); this is a cosmetic naming alias.
#
# Best-effort: never fails container init.
set -u

NEWUSER="${COVE_USERNAME:-}"
[ -z "${NEWUSER}" ] && exit 0

# Nothing to alias for the reserved names.
case "${NEWUSER}" in
  abc | root) exit 0 ;;
esac

# A ':' or whitespace would corrupt /etc/passwd. The Cove username charset
# forbids them, but guard anyway since we edit those files directly.
case "${NEWUSER}" in
  *[!a-zA-Z0-9._-]*)
    echo "[cove] username: '${NEWUSER}' has unsafe chars; skipping"
    exit 0
    ;;
esac

# Already present (name taken, or this script already ran)? Leave it alone.
if getent passwd "${NEWUSER}" >/dev/null 2>&1; then
  exit 0
fi

# abc's effective UID/GID after init-adduser applied PUID/PGID.
UID_N="$(id -u abc 2>/dev/null)" || exit 0
GID_N="$(id -g abc 2>/dev/null)" || exit 0
HOME_DIR="$(getent passwd abc | cut -d: -f6)"
SHELL_B="$(getent passwd abc | cut -d: -f7)"
[ -n "${HOME_DIR}" ] || HOME_DIR=/config
[ -n "${SHELL_B}" ] || SHELL_B=/bin/bash

passwd_line="${NEWUSER}:x:${UID_N}:${GID_N}::${HOME_DIR}:${SHELL_B}"
group_line="${NEWUSER}:x:${GID_N}:"

# Insert the alias immediately BEFORE abc's line so files-NSS first-match returns
# the Cove name for uid/gid lookups. Temp-file + mv keeps the replace atomic.
if awk -v line="${passwd_line}" \
  '/^abc:/ && !done {print line; done=1} {print}' /etc/passwd >/etc/passwd.cove; then
  mv /etc/passwd.cove /etc/passwd
else
  rm -f /etc/passwd.cove
  echo "[cove] username: failed to alias '${NEWUSER}' in /etc/passwd; skipping"
  exit 0
fi

# Group alias is optional (only affects the group column of `ls -l`); skip
# quietly if abc's group line isn't found.
if awk -v line="${group_line}" \
  '/^abc:/ && !done {print line; done=1} {print}' /etc/group >/etc/group.cove; then
  mv /etc/group.cove /etc/group
else
  rm -f /etc/group.cove
fi

echo "[cove] username: aliased '${NEWUSER}' to uid ${UID_N}/gid ${GID_N}"
exit 0
