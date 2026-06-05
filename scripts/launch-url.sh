#!/bin/bash
# Injected into link-type webtop containers.
# Waits for the desktop session, then opens Chromium to LAUNCH_URL in kiosk mode.
sleep 8
DISPLAY=:1 DBUS_SESSION_BUS_ADDRESS=autolaunch: \
  chromium-browser \
    --no-sandbox \
    --kiosk \
    --disable-session-crashed-bubble \
    --disable-infobars \
    "${LAUNCH_URL:-https://example.com}" \
  2>/dev/null &
