#!/bin/sh
# Pick the install target: the first disk that is not the medium we booted from.
# Runs inside the Debian installer (busybox sh).
SRC=$(mount | awk '$3 == "/cdrom" { print $1 }' | sed -E 's/p?[0-9]+$//')
for d in $(list-devices disk); do
  [ "$d" = "$SRC" ] && continue
  debconf-set partman-auto/disk "$d"
  logger -t cove "installing to $d (boot medium: ${SRC:-none})"
  exit 0
done
logger -t cove "no install disk found besides the boot medium"
