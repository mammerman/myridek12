"""
Constants for the MyRide K-12 integration.

Endpoints, headers, and the negotiate-then-watch flow below were reverse
engineered by legrego/myride-ha-bridge (https://github.com/legrego/myride-ha-bridge,
MIT) and re-confirmed against the real hub with a standalone spike script before
this integration was written. See README.md for what the spike found, including
the "stale heartbeat" behavior DEFAULT_STALE_AFTER exists to detect.
"""

from __future__ import annotations

from datetime import timedelta
from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "myride_k12"
ATTRIBUTION = "Data provided by Tyler Technologies' MyRide K-12 (unofficial)"

# ── Cognito ──────────────────────────────────────────────────────────────────
COGNITO_URL = "https://cognito-idp.us-east-1.amazonaws.com/"
COGNITO_CLIENT_ID = "3c5382gsq7g13djnejo98p2d98"
TOKEN_REFRESH_MARGIN = timedelta(minutes=5)

# ── MyRide API / hub ─────────────────────────────────────────────────────────
API_BASE = "https://myridek12.tylerapi.com"
HUB_URL = f"{API_BASE}/livevehiclehub"
APP_ORIGIN = "https://myridek12.tylerapp.com"
CLIENT_VERSION = "2026.2.17+bcb384"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)

# SignalR JSON protocol message types we care about (the rest we don't act on).
SIGNALR_TYPE_INVOCATION = 1
SIGNALR_TYPE_CLOSE = 7

RS = "\x1e"  # SignalR record separator
PING_INTERVAL = 15  # seconds
SERVER_TIMEOUT = 35  # seconds of total silence before we consider the socket dead
STUDENT_POLL_INTERVAL = timedelta(minutes=15)

# ── HTTP status codes (named so ruff doesn't flag them as magic numbers) ─────
HTTP_OK = 200
HTTP_UNAUTHORIZED = 401

# ── Config entry keys ────────────────────────────────────────────────────────
CONF_REFRESH_TOKEN = "refresh_token"
CONF_TENANT_ID = "tenant_id"

# ── Staleness ────────────────────────────────────────────────────────────────
# The spike found the hub re-broadcasts a bus's last known fix on an unknown
# heartbeat interval even when the vehicle isn't moving/reporting fresh GPS -
# logTime and lat/lng stay identical across messages while wall-clock lag climbs.
# There is no explicit "trip started"/"trip ended" event. So "is this live?" is
# derived from whether logTime has actually advanced recently, not from
# receiving *a* message or from speed > 0 (a stopped-but-live bus is speed 0
# too). Tune this once we have more full-route captures.
DEFAULT_STALE_AFTER = timedelta(minutes=2)
LIVE_RECHECK_INTERVAL = timedelta(
    seconds=30
)  # re-evaluate staleness even if no new message arrives

RECONNECT_RESET_AFTER = timedelta(
    minutes=1
)  # a connection alive this long resets backoff to its floor
MAX_BACKOFF = timedelta(minutes=1)
