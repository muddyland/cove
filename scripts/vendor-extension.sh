#!/bin/bash
# Refresh the vendored copy of the browser extension that Cove offers for
# download (Preferences → Browser extension).
#
# The extension is developed in its own repo; this copies the files a browser
# actually loads into extension/, and records which commit they came from. Run it
# after changing the extension, then commit the result:
#
#   scripts/vendor-extension.sh ../cove-browser-extension
set -euo pipefail

src=${1:-../cove-browser-extension}
dest="$(cd "$(dirname "$0")/.." && pwd)/extension"

[ -f "${src}/manifest.json" ] || { echo "not an extension checkout: ${src}" >&2; exit 1; }

rm -rf "${dest}"
mkdir -p "${dest}"
# Only what the browser loads: no tests, CI config, or icon build script.
cp "${src}/manifest.json" "${src}/README.md" "${dest}/"
cp -r "${src}/src" "${dest}/src"
mkdir -p "${dest}/icons"
cp "${src}/icons"/*.png "${dest}/icons/"

version=$(sed -n 's/.*"version": "\([^"]*\)".*/\1/p' "${dest}/manifest.json" | head -1)
commit=$(git -C "${src}" rev-parse --short HEAD 2>/dev/null || echo unknown)
cat > "${dest}/VENDOR" <<META
source: cove-browser-extension
version: ${version}
commit: ${commit}
vendored: $(date -u +%Y-%m-%d)
META
echo "vendored extension ${version} (${commit}) into ${dest}"
