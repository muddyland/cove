#!/bin/bash
# Cove thin client first boot: ask for Cove's address and an enrolment code on
# the console, then run Cove's installer. Runs once (until a device token exists).
set -u
ENV=/etc/cove-thin-client-firstboot.env
COVE_URL="" COVE_CODE=""
[ -r "$ENV" ] && . "$ENV"
LOG=/var/log/cove-firstboot.log

export TERM=linux
# Cove's palette, as far as a Linux console allows.
export NEWT_COLORS='
root=cyan,black
border=cyan,black
window=cyan,black
shadow=black,black
title=magenta,black
textbox=cyan,black
button=black,cyan
actbutton=black,magenta
compactbutton=cyan,black
entry=white,black
disentry=gray,black
label=cyan,black
helpline=magenta,black
roottext=magenta,black
'
BT="COVE // THIN CLIENT SETUP"
W=72

wait_network() {
  for _ in $(seq 1 60); do
    ip route show default 2>/dev/null | grep -q . && return 0
    whiptail --backtitle "$BT" --title "// NETWORK" --infobox "\nWaiting for a network connection..." 8 $W
    sleep 2
  done
  return 1
}

while true; do
  if ! wait_network; then
    whiptail --backtitle "$BT" --title "// NO NETWORK" --yesno \
      "\nNo network. Plug in a cable (or check the connection), then retry.\n\nRetry now?" 12 $W || exec bash
    continue
  fi
  URL=$(whiptail --backtitle "$BT" --title "// COVE ADDRESS" --inputbox \
    "\nThe address of your Cove server:" 10 $W "${COVE_URL:-https://}" 3>&1 1>&2 2>&3) || continue
  URL="${URL%/}"
  CODE="$COVE_CODE"
  if [ -z "$CODE" ]; then
    CODE=$(whiptail --backtitle "$BT" --title "// ENROLMENT CODE" --inputbox \
      "\nIn Cove: Admin > Thin clients > Enroll.\n\nType the code it shows (XXXX-XXXX-XXXX-XXXX):" 13 $W "" \
      3>&1 1>&2 2>&3) || continue
  fi
  CODE=$(printf '%s' "$CODE" | tr -d ' -' | tr '[:lower:]' '[:upper:]')
  clear
  echo ">> Contacting $URL ..."
  if ! curl -fsSL "$URL/thin-client/install.sh?code=$CODE" -o /tmp/cove-install.sh 2>>"$LOG"; then
    whiptail --backtitle "$BT" --title "// REJECTED" --msgbox \
      "\nCove didn't accept that.\n\nCheck the address, and that the code is right and hasn't expired or been used." 13 $W
    COVE_CODE=""
    continue
  fi
  if bash /tmp/cove-install.sh 2>&1 | tee -a "$LOG"; [ "${PIPESTATUS[0]}" -eq 0 ]; then
    rm -f "$ENV" /tmp/cove-install.sh
    systemctl disable cove-firstboot.service >/dev/null 2>&1
    echo ">> Enrolled. Rebooting into the kiosk..."
    sleep 3
    systemctl reboot
    exit 0
  fi
  whiptail --backtitle "$BT" --title "// INSTALL FAILED" --msgbox \
    "\nThe install failed. Details are in $LOG.\n\n$(tail -n 6 "$LOG" | cut -c1-66)" 18 $W
  COVE_CODE=""
done
