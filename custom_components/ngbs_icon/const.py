"""Constants for the NGBS iCON Thermostat integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "ngbs_icon"

BASE_URL: Final = "https://enzoldhazam.hu"
REQUEST_TIMEOUT: Final = 15
WRITE_DELAY: Final = 0.4
DEFAULT_SCAN_INTERVAL: Final = timedelta(seconds=10)
SESSION_RENEW_INTERVAL: Final = timedelta(hours=1)

CONF_ICON_ID: Final = "icon_id"
CONF_MANUAL_HC_SWITCH: Final = "manual_heating_cooling_switch"

ATTR_REQ: Final = "REQ"
ATTR_CE: Final = "CE"
ATTR_HC: Final = "HC"

# Hungarian login page marker — the cloud returns HTML instead of an error
# when the session is gone.
LOGIN_PAGE_MARKER: Final = "Bejelentkezés"

# How many coordinator polls to wait before concluding a manual season
# switchover was rejected by the controller.
SWITCHOVER_POLLS: Final = 4

MANUFACTURER: Final = "NGBS"
MODEL: Final = "iCON"
