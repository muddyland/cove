#!/bin/bash
# Injected into LinuxServer browser workspaces (/custom-cont-init.d) to clear a
# stale single-instance lock left in the persistent profile by an unclean halt.
#
# Why: halting a workspace removes the container without a graceful browser
# shutdown, so the profile in /config keeps a lock pointing at the (now dead)
# previous container — chromium-family browsers leave a "SingletonLock" symlink
# (target "<old-hostname>-<pid>"), Firefox a "lock"/".parentlock". On the next
# boot the browser sees the lock, assumes another instance owns the profile, and
# exits immediately — the desktop streams but the browser never appears. The
# image's own launcher only clears the lock when a browser is ALREADY running, so
# a fresh start never recovers.
#
# custom-cont-init.d runs (as root) during container init, before the desktop
# session starts the browser, so no browser holds these yet — clearing them is
# safe. We only ever remove lock files, never profile data. Opt-in per workspace.
#
# Best-effort: never fails container init.
set -u

CONFIG=/config

# Chromium family (Chromium, Brave, Edge, Chrome, Vivaldi…): the three Singleton*
# entries live at the profile root under ~/.config/<vendor>/.
find "$CONFIG/.config" -maxdepth 3 -type l \
  \( -name 'SingletonLock' -o -name 'SingletonCookie' -o -name 'SingletonSocket' \) \
  -print -delete 2>/dev/null || true

# Firefox: profile lock is a "lock" symlink plus a ".parentlock" file.
find "$CONFIG/.mozilla" -maxdepth 4 \
  \( -name 'lock' -o -name '.parentlock' \) \
  -print -delete 2>/dev/null || true

echo "[cove] clear-browser-lock: cleared any stale browser instance locks"
exit 0
