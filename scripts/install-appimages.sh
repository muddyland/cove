#!/bin/bash
# Injected into LinuxServer desktop workspaces (/custom-cont-init.d) to install
# AppImage apps listed in $COVE_APPIMAGES (newline/comma/space separated URLs).
#
# These containers are hardened (no /dev/fuse, no-new-privileges, dropped caps),
# so AppImages can't be run the usual FUSE-mounted way. Instead we EXTRACT each
# one (`--appimage-extract`, which needs no FUSE) into the persistent /config
# home and write a .desktop launcher so it appears in the desktop menu. Electron
# apps are launched with --no-sandbox (no unprivileged userns here).
#
# Best-effort: never fails container init; skips anything already installed.
set -u

raw="${COVE_APPIMAGES:-}"
raw="${raw//,/ }"                     # normalise commas to spaces
[ -z "${raw// /}" ] && exit 0

# Install in the BACKGROUND. custom-cont-init.d runs before the desktop starts,
# so downloading/extracting AppImages here would block the GUI from becoming
# ready and overrun the launch deadline. Backgrounding lets the desktop come up
# promptly; launchers appear as each AppImage finishes. Logged to ${log}.
log=/config/.cove-appimages.log

(
USER_NAME=abc
HOME_DIR=/config
APPS_DIR="${HOME_DIR}/.cove-appimages"
DESKTOP_DIR="${HOME_DIR}/.local/share/applications"

# Run a command as the unprivileged desktop user so files land in /config with
# the right ownership (init scripts themselves run as root).
run_as() { s6-setuidgid "${USER_NAME}" "$@"; }

run_as mkdir -p "${APPS_DIR}" "${DESKTOP_DIR}"

# Downloads have no built-in timeout; cap each so a stalled CDN can't hang
# container init forever.
timeout_cmd=""
command -v timeout >/dev/null 2>&1 && timeout_cmd="timeout 600"

# LinuxServer images ship curl but not always wget — prefer whichever exists.
download() {  # download <url> <dest>
  if command -v curl >/dev/null 2>&1; then
    run_as ${timeout_cmd} curl -fsSL -o "$2" "$1"
  elif command -v wget >/dev/null 2>&1; then
    run_as ${timeout_cmd} wget -q -O "$2" "$1"
  else
    echo "[cove] appimage: neither curl nor wget is available"
    return 1
  fi
}

for url in $raw; do
  case "$url" in
    http://*|https://*) ;;
    *) echo "[cove] appimage: skipping non-URL '$url'"; continue ;;
  esac

  file="$(basename "${url%%\?*}")"   # filename without any query string
  name="${file%.*}"                  # drop the .AppImage extension
  # Slugify for safe dir / desktop-id use.
  slug="$(printf '%s' "$name" | tr -c 'A-Za-z0-9._-' '_')"
  [ -z "$slug" ] && slug="appimage"
  dest="${APPS_DIR}/${slug}"

  if [ -x "${dest}/AppRun" ]; then
    echo "[cove] appimage: ${slug} already installed, skipping"
    continue
  fi

  appimage="${APPS_DIR}/${slug}.AppImage"
  echo "[cove] appimage: downloading ${url}"
  if ! download "${url}" "${appimage}"; then
    echo "[cove] appimage: download failed for ${url}"
    rm -f "${appimage}"
    continue
  fi
  run_as chmod +x "${appimage}"

  echo "[cove] appimage: extracting ${slug}"
  tmp="${APPS_DIR}/.extract-${slug}"
  rm -rf "${tmp}" "${dest}"
  run_as mkdir -p "${tmp}"
  # --appimage-extract drops 'squashfs-root' into the CWD. Some AppImage
  # runtimes exit non-zero even on a complete extraction, so judge success by
  # whether a runnable AppRun landed rather than by the exit code.
  ( cd "${tmp}" && run_as "${appimage}" --appimage-extract >/dev/null 2>&1 )
  if [ ! -x "${tmp}/squashfs-root/AppRun" ]; then
    echo "[cove] appimage: extract failed for ${slug} (out of disk?)"
    rm -rf "${tmp}" "${appimage}"
    continue
  fi
  run_as mv "${tmp}/squashfs-root" "${dest}"
  rm -rf "${tmp}" "${appimage}"

  # Prefer the app's own .desktop (carries a friendly Name); fall back to slug.
  disp="${name}"
  src_desktop="$(find "${dest}" -maxdepth 1 -name '*.desktop' 2>/dev/null | head -1)"
  if [ -n "${src_desktop}" ]; then
    got="$(grep -m1 '^Name=' "${src_desktop}" | cut -d= -f2-)"
    [ -n "${got}" ] && disp="${got}"
  fi
  # Reuse a shipped icon if present (absolute path so the menu can find it).
  icon="$(find "${dest}" -maxdepth 3 \( -name '*.png' -o -name '*.svg' \) 2>/dev/null | head -1)"

  out="${DESKTOP_DIR}/cove-${slug}.desktop"
  # Set APPDIR explicitly. Extracted AppImages auto-detect their AppDir by
  # walking up from $0 looking for a dir containing $1 — but $1 here is
  # "--no-sandbox", which matches nothing, so detection collapses to "" and the
  # app binary path becomes "/<bin>" (works from a CLI with no args, fails from
  # the menu). Exporting APPDIR skips that broken auto-detection entirely.
  run_as tee "${out}" >/dev/null <<EOF
[Desktop Entry]
Type=Application
Name=${disp}
Exec=env APPDIR=${dest} ${dest}/AppRun --no-sandbox %U
Icon=${icon}
Terminal=false
Categories=AudioVideo;Network;Utility;
EOF
  run_as chmod +x "${out}"
  echo "[cove] appimage: installed ${disp}"
done
) >>"${log}" 2>&1 &

echo "[cove] appimage: installing in background; log: ${log}"
exit 0
