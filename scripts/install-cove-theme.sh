#!/bin/bash
# Injected into LinuxServer Selkies workspaces (/custom-cont-init.d) to restyle the
# in-stream Selkies dashboard/menu (the side panel: Video/Screen/Audio settings…)
# with Cove's cyberpunk theme — neon cyan on deep navy, matching the surrounding
# Cove UI. The menu lives inside the streamed desktop's own web app (a cross-origin
# iframe to the SPA), so it can only be themed from inside the container.
#
# How: the Selkies dashboard is a Vite SPA whose entire look is driven by CSS custom
# properties defined in its hashed assets/index-*.css. We APPEND an override block
# to that file so our rules, coming last, win — no need to know the hashed filename
# or reverse-engineer the DOM.
#
# Current builds define a layer of PRIMITIVES (--bg, --surface, --surface-inset,
# --border-soft, --text, --accent, …) and derive the component variables from them
# (--sidebar-bg: var(--surface)). Overriding only the derived leaves, as this script
# first did, leaves every newer component — the stream-stats tiles, meters and
# graphs, the files modal, the gamepad — reading stock primitives, i.e. half a menu
# in Cove's palette and half in Selkies'. So we set the primitives AND keep the leaf
# overrides, which are all that older images (where nothing is derived) have.
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
  /* Primitives. Everything current derives from these; setting them is what
     keeps components we have never heard of on-palette. */
  --bg: #06060f;
  --surface: #0b0b1e;
  --surface-alt: #10102a;
  --surface-inset: #06060f;
  --border-soft: #1c1c42;
  --border-strong: #2c2c66;
  --text: #c8d8ff;
  --text-muted: #8aa0d0;
  --accent: #00f5ff;
  --accent-strong: #7fffff;
  --accent-text: #00f5ff;
  --accent-text-strong: #7fffff;
  --accent-soft: rgba(0, 245, 255, .14);
  --accent-border: rgba(0, 245, 255, .45);
  /* Links and the wordmark gradient read these; keep them cool rather than the
     stock violet, which clashes with the cyan. */
  --violet: #8fb4ff;
  --violet-strong: #bcd4ff;
  --wordmark-from: #00f5ff;
  --wordmark-to: #8fb4ff;
  --item-bg-hover: #16163a;
  --error-color: #ff2055;
  /* The stock caret is a data: SVG stroked in the stock muted grey. */
  --select-arrow: url("data:image/svg+xml;charset=US-ASCII,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%2212%22%20height%3D%228%22%3E%3Cpath%20fill%3D%22none%22%20stroke%3D%22%23c8d8ff%22%20stroke-width%3D%222%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%20d%3D%22M1%201.5l5%205%205-5%22%2F%3E%3C%2Fsvg%3E");
  /* On-screen gamepad tester. */
  --gamepad-shell-fill: #10102a;
  --gamepad-shell-stroke: #1c1c42;
  --gamepad-pad-fill: #0e5f75;
  --gamepad-pad-stroke: #00f5ff;
  --gamepad-pad-pressed-fill: #7fffff;
  --gamepad-stick-base-fill: #123049;
  --gamepad-stick-top-fill: #00c0d8;
  --gamepad-stick-top-stroke: #7fffff;

  /* Component variables. Derived from the primitives on current builds, defined
     outright on older ones — so these stay, and must agree with the above. */
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
  --bg: #eef2fb;
  --surface: #ffffff;
  --surface-alt: #f4f7ff;
  --surface-inset: #ffffff;
  --border-soft: #c3d0f0;
  --border-strong: #93a6d0;
  --text: #10102a;
  --text-muted: #4a5a80;
  --accent: #0091a8;
  --accent-strong: #00b4d0;
  --accent-soft: rgba(0, 145, 168, .12);
  --accent-border: rgba(0, 145, 168, .45);
  --violet: #3f57b5;
  --violet-strong: #2c3f8f;
  --wordmark-from: #0091a8;
  --wordmark-to: #3f57b5;
  --item-bg-hover: #e4ebff;
  --error-color: #c01030;
  --select-arrow: url("data:image/svg+xml;charset=US-ASCII,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%2212%22%20height%3D%228%22%3E%3Cpath%20fill%3D%22none%22%20stroke%3D%22%2310102a%22%20stroke-width%3D%222%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%20d%3D%22M1%201.5l5%205%205-5%22%2F%3E%3C%2Fsvg%3E");
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

/* The stream-stats panel scopes its own series/threshold colours, so they can
   only be reached inside that selector. */
.stream-stats {
  --stat-series-1: #00f5ff;
  --stat-series-2: #8fb4ff;
  --stat-good: #00ff9d;
  --stat-warn: #ffaa00;
}
.theme-light .stream-stats {
  --stat-series-1: #0091a8;
  --stat-series-2: #3f57b5;
  --stat-good: #0a7d55;
  --stat-warn: #9a6400;
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
/* The stream-stats readouts, given the same neon treatment as the controls. */
.stream-tile {
  background-image: linear-gradient(180deg, rgba(0, 245, 255, .05), transparent);
}
.stream-meter-fill { box-shadow: 0 0 6px rgba(0, 245, 255, .5); }
.stream-graph-line { filter: drop-shadow(0 0 3px rgba(0, 245, 255, .45)); }

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
  if [ -z "${target}" ]; then
    # The "wish" dashboard was rebuilt on a different token set entirely
    # (--background/--card/--primary) and shares no variable with this theme.
    # It is only served when DASHBOARD names it, so say which it is rather than
    # reporting a generic miss.
    if grep -lq -- '--card-foreground' "${dir}"/assets/*.css 2>/dev/null; then
      echo "[cove] selkies theme: ${dir} uses the newer token set; not themed (only used when DASHBOARD selects it)"
    fi
    continue
  fi
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
