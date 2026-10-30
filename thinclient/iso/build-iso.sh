#!/usr/bin/env bash
# Build a Cove thin client install image from the official Debian netinst ISO.
#
# The result boots (BIOS or UEFI, from USB or CD) to Debian's usual menu with
# one extra entry on top: "Install Cove thin client (ERASES THE DISK)". That
# entry installs a minimal Debian unattended, then on first boot asks on the
# console for Cove's address and an enrolment code and runs Cove's installer.
# Debian's own entries are left untouched and still ask before touching a disk.
#
# Needs xorriso, curl, openssl and sha256sum, or Docker (it then runs itself in
# a debian:trixie container).
#
#   thinclient/iso/build-iso.sh --cove-url https://cove.example.com [options]
#
# Options:
#   --cove-url URL        Cove's address, prefilled on first boot (required)
#   --code CODE           bake in an enrolment code (mint one with a 7-day
#                         lifetime); first boot then needs no typing at all
#   --out FILE            output image (default: cove-thin-client.iso)
#   --iso FILE            use this Debian netinst ISO instead of downloading
#   --hostname NAME       default: cove-tc
#   --keymap MAP          console/kiosk keyboard, e.g. us, gb, de (default: us)
#   --locale LOCALE       default: en_US.UTF-8
#   --timezone TZ         e.g. Europe/London (default: Etc/UTC)
#   --password PASS       password for the "coveadmin" sudo account
#                         (default: generated and printed at the end)
#   --ssh-key FILE        authorized_keys for coveadmin; also installs SSH,
#                         keys only
#   --ca-file FILE        Cove's certificate (or private CA) if it isn't
#                         publicly trusted, e.g. certs/cove.crt from the LAN
#                         TLS setup; trusted by the installed system
#
# Write the image to a USB stick with, for example:
#   sudo dd if=cove-thin-client.iso of=/dev/sdX bs=4M conv=fsync status=progress
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MIRROR="${COVE_DEBIAN_MIRROR:-https://cdimage.debian.org/debian-cd/current/amd64/iso-cd}"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/cove-thin-client"

COVE_URL="" CODE="" OUT="cove-thin-client.iso" SRC_ISO="" HOST="cove-tc" KEYMAP="us"
LOCALE="en_US.UTF-8" TIMEZONE="Etc/UTC" PASSWORD="" SSH_KEY="" CA_FILE=""
ORIG_ARGS=("$@")

die() { echo "build-iso: $*" >&2; exit 1; }
while [ $# -gt 0 ]; do
  case "$1" in
    --cove-url) COVE_URL="${2%/}"; shift 2 ;;
    --code) CODE="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --iso) SRC_ISO="$2"; shift 2 ;;
    --hostname) HOST="$2"; shift 2 ;;
    --keymap) KEYMAP="$2"; shift 2 ;;
    --locale) LOCALE="$2"; shift 2 ;;
    --timezone) TIMEZONE="$2"; shift 2 ;;
    --password) PASSWORD="$2"; shift 2 ;;
    --ssh-key) SSH_KEY="$2"; shift 2 ;;
    --ca-file) CA_FILE="$2"; shift 2 ;;
    -h|--help) sed -n '2,34p' "$0"; exit 0 ;;
    *) die "unknown option $1 (see --help)" ;;
  esac
done
[ -n "$COVE_URL" ] || die "--cove-url is required"
# https only: over plain http, anything on the path could swap the agent (its
# checksum arrives in the same response) and run code as root on the device.
case "$COVE_URL" in https://*) ;; *) die "--cove-url must start with https:// (see --ca-file for a self-signed Cove)" ;; esac
[[ "$HOST" =~ ^[a-zA-Z0-9-]{1,63}$ ]] || die "--hostname must be a plain host name"
[[ "$KEYMAP" =~ ^[a-z0-9_()-]+$ ]] || die "--keymap looks wrong"
[[ "$TIMEZONE" =~ ^[A-Za-z0-9_/+-]+$ ]] || die "--timezone looks wrong"
[ -z "$SSH_KEY" ] || [ -r "$SSH_KEY" ] || die "cannot read --ssh-key $SSH_KEY"
[ -z "$CA_FILE" ] || grep -q "BEGIN CERTIFICATE" "$CA_FILE" 2>/dev/null || die "--ca-file must be a PEM certificate"

# ── Run in Docker when the tools are missing ────────────────────────────────
need=(xorriso curl openssl sha256sum)
missing=0
for t in "${need[@]}"; do command -v "$t" >/dev/null 2>&1 || missing=1; done
if [ "$missing" = 1 ] && [ -z "${COVE_ISO_IN_DOCKER:-}" ]; then
  command -v docker >/dev/null 2>&1 || die "install ${need[*]} (or Docker) first"
  out_abs="$(cd "$(dirname "$OUT")" && pwd)/$(basename "$OUT")"
  mkdir -p "$CACHE"
  mounts=(-v "$HERE:/cove-iso:ro" -v "$(dirname "$out_abs"):/out" -v "$CACHE:/cache")
  args=()
  skip=0
  for a in "${ORIG_ARGS[@]}"; do
    if [ "$skip" = 1 ]; then skip=0; continue; fi
    case "$a" in --out|--iso|--ssh-key|--ca-file) skip=1; continue ;; esac
    args+=("$a")
  done
  args+=(--out "/out/$(basename "$out_abs")")
  if [ -n "$SRC_ISO" ]; then
    src_abs="$(cd "$(dirname "$SRC_ISO")" && pwd)/$(basename "$SRC_ISO")"
    mounts+=(-v "$src_abs:/src.iso:ro"); args+=(--iso /src.iso)
  fi
  if [ -n "$SSH_KEY" ]; then
    key_abs="$(cd "$(dirname "$SSH_KEY")" && pwd)/$(basename "$SSH_KEY")"
    mounts+=(-v "$key_abs:/ssh.pub:ro"); args+=(--ssh-key /ssh.pub)
  fi
  if [ -n "$CA_FILE" ]; then
    ca_abs="$(cd "$(dirname "$CA_FILE")" && pwd)/$(basename "$CA_FILE")"
    mounts+=(-v "$ca_abs:/ca.crt:ro"); args+=(--ca-file /ca.crt)
  fi
  echo ">> tools missing here; building inside debian:trixie"
  exec docker run --rm "${mounts[@]}" -e COVE_ISO_IN_DOCKER=1 -e XDG_CACHE_HOME=/ -e HOME=/ \
    -e COVE_ISO_OUT_SHOWN="$out_abs" \
    -e COVE_DEBIAN_MIRROR="$MIRROR" debian:trixie bash -c \
    'apt-get update -qq && apt-get install -y -qq --no-install-recommends xorriso curl openssl ca-certificates \
       >/dev/null && CACHE=/cache exec bash /cove-iso/build-iso.sh "$@"' _ "${args[@]}"
fi
[ -n "${COVE_ISO_IN_DOCKER:-}" ] && CACHE=/cache

# ── The Debian netinst ISO ──────────────────────────────────────────────────
if [ -z "$SRC_ISO" ]; then
  mkdir -p "$CACHE"
  echo ">> finding the current Debian netinst image"
  sums="$(curl -fsSL "$MIRROR/SHA256SUMS")"
  name="$(printf '%s\n' "$sums" | awk '$2 ~ /^debian-[0-9.]+-amd64-netinst\.iso$/ { print $2; exit }')"
  [ -n "$name" ] || die "no netinst image listed at $MIRROR"
  want="$(printf '%s\n' "$sums" | awk -v n="$name" '$2 == n { print $1 }')"
  SRC_ISO="$CACHE/$name"
  if [ ! -f "$SRC_ISO" ] || [ "$(sha256sum "$SRC_ISO" | cut -d' ' -f1)" != "$want" ]; then
    echo ">> downloading $name"
    if [ -t 2 ]; then progress=--progress-bar; else progress=-sS; fi
    curl -fL "$progress" -o "$SRC_ISO.part" "$MIRROR/$name"
    [ "$(sha256sum "$SRC_ISO.part" | cut -d' ' -f1)" = "$want" ] || die "checksum mismatch for $name"
    mv "$SRC_ISO.part" "$SRC_ISO"
  fi
  echo ">> $name (sha256 verified)"
fi
[ -r "$SRC_ISO" ] || die "cannot read $SRC_ISO"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/cove"

# ── Our files ───────────────────────────────────────────────────────────────
GENERATED_PW=""
if [ -z "$PASSWORD" ]; then
  PASSWORD="$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-20)"
  GENERATED_PW=1
fi
HASH="$(printf '%s' "$PASSWORD" | openssl passwd -6 -stdin)"
SSH_TASK=""
[ -n "$SSH_KEY" ] && SSH_TASK=", ssh-server"

preseed="$(cat "$HERE/preseed.cfg.in")"
preseed="${preseed//__LOCALE__/$LOCALE}"
preseed="${preseed//__KEYMAP__/$KEYMAP}"
preseed="${preseed//__HOSTNAME__/$HOST}"
preseed="${preseed//__TIMEZONE__/$TIMEZONE}"
preseed="${preseed//__PWHASH__/$HASH}"
preseed="${preseed//__SSH_TASK__/$SSH_TASK}"
printf '%s\n' "$preseed" > "$WORK/cove/preseed.cfg"
for f in early.sh late.sh firstboot.sh cove-firstboot.service; do cp "$HERE/$f" "$WORK/cove/$f"; done
{
  printf 'COVE_URL=%q\n' "$COVE_URL"
  printf 'COVE_CODE=%q\n' "$CODE"
} > "$WORK/cove/firstboot.env"
if [ -n "$SSH_KEY" ]; then cp "$SSH_KEY" "$WORK/cove/authorized_keys"; else : > "$WORK/cove/authorized_keys"; fi
if [ -n "$CA_FILE" ]; then cp "$CA_FILE" "$WORK/cove/ca.crt"; else : > "$WORK/cove/ca.crt"; fi

# ── Boot menus: our entry first, Debian's untouched below ───────────────────
xorriso -osirrox on -indev "$SRC_ISO" \
  -extract /isolinux/txt.cfg "$WORK/txt.cfg" \
  -extract /boot/grub/grub.cfg "$WORK/grub.cfg" \
  -extract /md5sum.txt "$WORK/md5sum.txt" >/dev/null 2>&1 || die "unexpected ISO layout (not a Debian netinst?)"
chmod u+w "$WORK"/*.cfg "$WORK/md5sum.txt"

KARGS="auto=true priority=critical file=/cdrom/cove/preseed.cfg locale=$LOCALE keymap=$KEYMAP hostname=$HOST domain="
{
  cat <<EOF
label cove
	menu label ^Install Cove thin client (ERASES THE DISK)
	menu default
	kernel /install.amd/vmlinuz
	append vga=788 initrd=/install.amd/initrd.gz $KARGS --- quiet
EOF
  sed '/menu default/d' "$WORK/txt.cfg"
} > "$WORK/txt.new" && mv "$WORK/txt.new" "$WORK/txt.cfg"

awk -v kargs="$KARGS" '
  !done && /^menuentry/ {
    print "menuentry --hotkey=c '\''Install Cove thin client (ERASES THE DISK)'\'' {"
    print "    set background_color=black"
    print "    linux    /install.amd/vmlinuz " kargs " --- quiet"
    print "    initrd   /install.amd/initrd.gz"
    print "}"
    done = 1
  }
  { print }
' "$WORK/grub.cfg" > "$WORK/grub.new" && mv "$WORK/grub.new" "$WORK/grub.cfg"
grep -q "Install Cove thin client" "$WORK/grub.cfg" || die "could not add the UEFI menu entry"

# Keep the installer's integrity check ("Check the CD-ROM") meaningful.
sed -i -E '\# \./(isolinux/txt\.cfg|boot/grub/grub\.cfg)$#d' "$WORK/md5sum.txt"
(
  cd "$WORK"
  md5sum txt.cfg | sed 's# txt.cfg# ./isolinux/txt.cfg#'
  md5sum grub.cfg | sed 's# grub.cfg# ./boot/grub/grub.cfg#'
  for f in cove/*; do md5sum "$f" | sed "s# $f# ./$f#"; done
) >> "$WORK/md5sum.txt"

# ── Write the image (boot records replayed from the original: BIOS + UEFI,
#    hybrid for USB) ────────────────────────────────────────────────────────
rm -f "$OUT"
xorriso -indev "$SRC_ISO" -outdev "$OUT" \
  -map "$WORK/txt.cfg" /isolinux/txt.cfg \
  -map "$WORK/grub.cfg" /boot/grub/grub.cfg \
  -map "$WORK/md5sum.txt" /md5sum.txt \
  -map "$WORK/cove" /cove \
  -boot_image any replay >/dev/null 2>&1 || die "xorriso failed to write $OUT"

echo
echo ">> wrote ${COVE_ISO_OUT_SHOWN:-$OUT} ($(du -h "$OUT" | cut -f1))"
echo "   Cove:       $COVE_URL"
echo "   enrolment:  ${CODE:+baked in (asked again if it has expired)}${CODE:-asked on first boot}"
echo "   admin user: coveadmin${GENERATED_PW:+  password: $PASSWORD  (generated; keep it somewhere safe)}"
[ -n "$SSH_KEY" ] && echo "   ssh:        key-only logins for coveadmin"
[ -n "$CA_FILE" ] && echo "   tls:        trusts $(basename "$CA_FILE") for Cove"
echo "   Boot it and pick \"Install Cove thin client\". It erases the first disk that isn't the USB stick."
