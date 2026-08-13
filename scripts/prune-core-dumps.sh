#!/bin/bash
# Injected into every workspace (/custom-cont-init.d) to delete core dumps that
# crashed desktop apps leave in the persistent /config volume.
#
# Why: kernel.core_pattern is a HOST-wide setting (it is not namespaced), and on
# most hosts it is the default "core", which writes the dump to the crashing
# process's cwd — for a LinuxServer desktop that is /config, the one directory
# that persists across workspace restarts. Chromium and Electron apps (VSCodium,
# Code, Obsidian…) map their whole heap, so a single crash lands a ~1 GB
# core.<pid>, and a workspace that has been up for weeks quietly accumulates
# tens of gigabytes of them. Nothing in the image ever removes them.
#
# custom-cont-init.d runs (as root) during container init, before the desktop
# session starts, so no live process owns these files yet. Runs early (02-) so
# the space is back before the package/AppImage installers need it.
#
# Only files matching /config/core.<pid> that are genuinely ELF core dumps are
# removed — never a directory, never a user file that merely starts with "core.".
# Set COVE_CORE_DUMP_RETAIN_DAYS to keep recent dumps for debugging (default 0,
# meaning delete all).
#
# Best-effort: never fails container init.
set -u

CONFIG=/config
retain="${COVE_CORE_DUMP_RETAIN_DAYS:-0}"
case "$retain" in
  ''|*[!0-9]*) retain=0 ;;
esac

have_od=1
command -v od >/dev/null 2>&1 || have_od=0

# An ELF core dump: magic 7f 45 4c 46, then e_type == ET_CORE (4) at offset 16.
# e_type is 2 bytes whose byte order follows EI_DATA, so accept both orderings.
is_core_dump() {
  [ "$have_od" -eq 1 ] || return 0  # can't verify; the core.<pid> name already gated it
  bytes=$(od -An -N18 -tx1 "$1" 2>/dev/null | tr -d '\n ')
  case "$bytes" in
    7f454c46*) ;;
    *) return 1 ;;
  esac
  etype=$(printf '%s' "$bytes" | cut -c33-36)
  [ "$etype" = "0400" ] || [ "$etype" = "0004" ]
}

removed=0
freed_kb=0

for f in "$CONFIG"/core.*; do
  # No matches: the glob comes back literal.
  [ -f "$f" ] || continue

  # Suffix must be a bare PID — skips core.log, core.conf, core.bak, etc.
  suffix="${f##*/core.}"
  case "$suffix" in
    ''|*[!0-9]*) continue ;;
  esac

  # Keep dumps newer than the retention window (-mtime -N = modified < N days ago).
  if [ "$retain" -gt 0 ] && [ -n "$(find "$f" -mtime "-$retain" 2>/dev/null)" ]; then
    continue
  fi

  is_core_dump "$f" || continue

  # du, not ls: core dumps are sparse, so apparent size wildly overstates the
  # blocks actually charged against the volume.
  kb=$(du -k "$f" 2>/dev/null | cut -f1)
  case "$kb" in ''|*[!0-9]*) kb=0 ;; esac

  if rm -f "$f" 2>/dev/null; then
    removed=$((removed + 1))
    freed_kb=$((freed_kb + kb))
  fi
done

if [ "$removed" -gt 0 ]; then
  echo "[cove] prune-core-dumps: removed $removed core dump(s), freed $((freed_kb / 1024)) MB"
else
  echo "[cove] prune-core-dumps: no core dumps to remove"
fi

exit 0
