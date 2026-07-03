#!/bin/bash
# Injected into LinuxServer MATE desktop workspaces (/custom-cont-init.d) to fix
# GTK "dark mode won't stick — it reverts to white".
#
# Why: the base image runs `xsettingsd` as the desktop's XSETTINGS provider on
# everything that isn't XFCE/Wayland, and it is the ONLY working XSETTINGS manager
# on MATE (mate-settings-daemon does not own the selection here). But the image's
# xsettingsd config carries no Net/ThemeName — so GTK apps never receive the theme
# the user picks in MATE Appearance and fall back to /etc/gtk-3.0/settings.ini
# (light "Yaru"). The window manager (marco) reads gsettings directly and does go
# dark, but app *content* stays white — the exact "reverts to white" symptom.
#
# Fix: install a tiny per-session agent that mirrors the MATE gtk-theme into
# xsettingsd's own config (~/.xsettingsd) and SIGHUPs it, both at login and on
# every change. Result: whatever theme the user selects in MATE Appearance —
# light or dark — is published to GTK apps immediately and sticks across restarts.
# We don't pin a theme; we make the user's own choice actually take effect.
#
# Runs as root at container init; installs user-owned files under /config (which
# persists). Self-guards: a no-op unless this is a MATE desktop with xsettingsd on
# X11 (not Wayland). Best-effort; never fails init.
set -u

command -v mate-session >/dev/null 2>&1 || { echo "[cove] mate-xsettings: not a MATE desktop; skipping"; exit 0; }
command -v xsettingsd  >/dev/null 2>&1 || { echo "[cove] mate-xsettings: no xsettingsd; skipping"; exit 0; }
[ "${PIXELFLUX_WAYLAND:-}" = "true" ] && { echo "[cove] mate-xsettings: Wayland session; skipping"; exit 0; }

BIN=/config/.local/bin
AUTO=/config/.config/autostart
mkdir -p "$BIN" "$AUTO"

# The per-session agent. Reads the MATE-selected theme and republishes it through
# xsettingsd, then watches for changes so MATE Appearance edits apply live.
cat > "$BIN/cove-mate-theme-sync.sh" <<'EOS'
#!/bin/bash
set -u
XS="$HOME/.xsettingsd"
apply() {
  local gtk icon xft
  gtk=$(gsettings get org.mate.interface gtk-theme 2>/dev/null | tr -d "'\"")
  icon=$(gsettings get org.mate.interface icon-theme 2>/dev/null | tr -d "'\"")
  [ -n "$gtk" ] || return 0
  [ -n "$icon" ] || icon="$gtk"
  # Preserve any existing Xft/* tuning (the image's Xft/DPI etc.); we own only the
  # theme keys. The theme name is authoritative for light/dark (e.g. Yaru-MATE-dark)
  # — we deliberately do NOT set Gtk/ApplicationPreferDarkTheme, which would ask GTK
  # for a dark variant *on top of* an already-dark theme and render broken.
  xft=$(grep -E '^Xft/' "$XS" 2>/dev/null)
  [ -n "$xft" ] || xft="Xft/DPI 98304"
  {
    echo "Net/ThemeName \"$gtk\""
    echo "Net/IconThemeName \"$icon\""
    printf '%s\n' "$xft"
  } > "$XS"
  pkill -HUP -x xsettingsd 2>/dev/null
}
apply
# Republish on every appearance change. Monitor the whole org.mate.interface
# schema (MATE Appearance updates gtk-theme + icon-theme together) and debounce so
# the sibling keys have all settled before we read them.
gsettings monitor org.mate.interface 2>/dev/null | while read -r _; do
  sleep 0.3
  apply
done
EOS
chmod +x "$BIN/cove-mate-theme-sync.sh"

cat > "$AUTO/cove-mate-theme-sync.desktop" <<EOS
[Desktop Entry]
Type=Application
Name=Cove MATE theme sync
Comment=Publishes the MATE theme to xsettingsd so GTK apps honor dark mode
Exec=$BIN/cove-mate-theme-sync.sh
X-MATE-Autostart-enabled=true
NoDisplay=true
EOS

chown -R abc:abc "$BIN" "$AUTO" 2>/dev/null || true
echo "[cove] mate-xsettings: installed theme-sync agent (GTK apps will follow MATE Appearance)"
exit 0
