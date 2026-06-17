#!/bin/bash
# Injected into LinuxServer Selkies workspaces (/custom-cont-init.d) to restyle the
# in-stream Selkies dashboard/menu (the side panel: Video/Screen/Audio settings…)
# with Cove's cyberpunk theme — neon cyan on deep navy, matching the surrounding
# Cove UI. The menu lives inside the streamed desktop's own web app (a cross-origin
# iframe to the SPA), so it can only be themed from inside the container.
#
# How: the Selkies dashboard is a Vite SPA whose entire look is driven by CSS custom
# properties (--sidebar-*, --section-*, --button-*, …) defined in its hashed
# assets/index-*.css. We APPEND an override block to that file so our rules, coming
# last, win — no need to know the hashed filename or reverse-engineer the DOM.
#
# We patch both the dashboard *templates* (/usr/share/selkies/selkies-dashboard*)
# and the live web root (/usr/share/selkies/web): init-nginx copies a template into
# `web` at boot, and it runs unordered relative to this script, so touching both
# covers either order. Marker-guarded (idempotent) and best-effort — a Selkies
# upgrade that drops the theme variables, or a non-Selkies image, just leaves the
# stock look. Never fails container init.
set -u

MARKER="COVE-CYBERPUNK"

# The override block. Avoids @import/webfonts on purpose: an appended @import is
# ignored by browsers (must lead the sheet) and the container may have no egress,
# so we lean on a monospace stack already present in the image.
cove_css() {
cat <<'CSS'

/* ===== COVE-CYBERPUNK ===== injected by Cove to match the dashboard UI */
:root, .theme-dark {
  --sidebar-bg: #0b0b1e;
  --sidebar-text: #c8d8ff;
  --sidebar-header-color: #00f5ff;
  --sidebar-border: #1c1c42;
  --sidebar-shadow: rgba(0, 245, 255, .25);
  --section-bg: #10102a;
  --item-border: #1c1c42;
  --input-bg: #06060f;
  --input-text: #c8d8ff;
  --input-border: #1c1c42;
  --button-bg: #00f5ff;
  --button-text: #06060f;
  --button-hover-bg: #7fffff;
  --pre-bg: #06060f;
  --pre-text: #c8d8ff;
  --tooltip-bg: #0b0b1e;
  --tooltip-text: #c8d8ff;
  --tooltip-border: #00f5ff;
  --icon-sun-color: #ffaa00;
  --icon-moon-color: #00f5ff;
  --slider-track-color: #10102a;
  --slider-thumb-color: #00f5ff;
  --notification-progress-bg: #1c1c42;
  --notification-progress-fill: #00f5ff;
  --notification-success-color: #00ff9d;
  --notification-error-color: #ff2055;
  --notification-warn-color: #ffaa00;
  --notification-shadow: rgba(0, 0, 0, .6);
  --notification-close-hover-bg: rgba(0, 245, 255, .12);
}
/* Light mode kept on-brand (deep teal accent on a cool white) for the sun toggle. */
.theme-light {
  --sidebar-bg: #eef2fb;
  --sidebar-text: #10102a;
  --sidebar-header-color: #0091a8;
  --sidebar-border: #c3d0f0;
  --section-bg: #ffffff;
  --item-border: #c3d0f0;
  --input-bg: #ffffff;
  --input-text: #10102a;
  --input-border: #c3d0f0;
  --button-bg: #0091a8;
  --button-text: #ffffff;
  --button-hover-bg: #00b4d0;
  --slider-thumb-color: #0091a8;
  --tooltip-border: #0091a8;
}

/* --- Neon flourishes (beyond the palette swap) --------------------------- */
.sidebar {
  font-family: "Share Tech Mono", ui-monospace, "Courier New", monospace;
  border-right: 1px solid var(--sidebar-header-color);
  background-image: linear-gradient(180deg, rgba(0, 245, 255, .05), transparent 260px);
}
.sidebar h2 {
  letter-spacing: 3px;
  text-transform: uppercase;
  text-shadow: 0 0 6px rgba(0, 245, 255, .7), 0 0 16px rgba(0, 245, 255, .35);
}
.sidebar h3,
.sidebar-section-header h3 {
  letter-spacing: 1.5px;
  text-transform: uppercase;
  font-size: 1em;
  text-shadow: 0 0 5px rgba(0, 245, 255, .4);
}
.action-button:hover { box-shadow: 0 0 6px rgba(0, 245, 255, .4); }
.action-button.active,
.header-action-button.active {
  box-shadow: 0 0 8px rgba(0, 245, 255, .6), 0 0 18px rgba(0, 245, 255, .3);
}
.toggle-indicator {
  box-shadow: 0 0 8px var(--sidebar-header-color), 0 0 16px rgba(0, 245, 255, .5);
}
.resolution-button:hover {
  color: var(--sidebar-header-color);
  box-shadow: 0 0 6px rgba(0, 245, 255, .35);
}
/* Neon-glow scrollbar inside the panel. */
.sidebar::-webkit-scrollbar { width: 8px; }
.sidebar::-webkit-scrollbar-track { background: #06060f; }
.sidebar::-webkit-scrollbar-thumb {
  background: #1c1c42;
  border-radius: 4px;
  box-shadow: inset 0 0 4px rgba(0, 245, 255, .4);
}
.sidebar::-webkit-scrollbar-thumb:hover { background: var(--sidebar-header-color); }
/* ===== /COVE-CYBERPUNK ===== */
CSS
}

patched=0
seen_dashboard=0
for dir in \
  /usr/share/selkies/selkies-dashboard \
  /usr/share/selkies/selkies-dashboard-wish \
  /usr/share/selkies/web
do
  [ -d "${dir}/assets" ] || continue
  seen_dashboard=1
  # The one stylesheet that defines the dashboard's theme variables (the SPA may
  # ship several hashed CSS chunks — vendor, radix-ui — but only this one carries
  # --sidebar-header-color).
  target="$(grep -l 'sidebar-header-color' "${dir}"/assets/*.css 2>/dev/null | head -n1)"
  [ -n "${target}" ] || continue
  if grep -q "${MARKER}" "${target}" 2>/dev/null; then
    continue  # already themed (this dir, or copied from an already-themed template)
  fi
  if cove_css >>"${target}" 2>/dev/null; then
    echo "[cove] selkies theme: applied to ${target}"
    patched=1
  fi
done

if [ "${seen_dashboard}" != 1 ]; then
  echo "[cove] selkies theme: no Selkies dashboard found; skipping (non-Selkies image)"
elif [ "${patched}" != 1 ]; then
  echo "[cove] selkies theme: dashboard already themed or theme variables not found"
fi
exit 0
