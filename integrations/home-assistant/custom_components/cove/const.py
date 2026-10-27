"""Constants for the Cove integration."""

from datetime import timedelta

DOMAIN = "cove"

SCAN_INTERVAL = timedelta(seconds=30)
# A thin client acts on a command within a second or two of it being queued;
# refresh after this long so its new state shows without waiting a full scan.
COMMAND_REFRESH_DELAY = 3
# How long a switch shows the state it was just set to while the device catches up.
OPTIMISTIC_SECONDS = 20
# "Keep awake" on: Cove's maximum. Automations turn it off when it's no longer wanted.
KEEP_AWAKE_MINUTES = 24 * 60
