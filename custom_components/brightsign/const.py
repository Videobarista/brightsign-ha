"""Constants for the BrightSign integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "brightsign"
MANUFACTURER = "BrightSign"

CONF_MAC = "mac"
CONF_SERIAL = "serial"
CONF_REST_SCHEME = "rest_scheme"
CONF_HAS_LWS = "has_lws"
CONF_FINGERPRINT = "fingerprint"
CONF_HAS_LEGACY = "has_legacy"

DEFAULT_PASSWORD = ""
DWS_USERNAME = "admin"

SCAN_INTERVAL = timedelta(seconds=30)
REQUEST_TIMEOUT = 8

# Local Web Server of a BrightAuthor presentation (only when enabled in the presentation)
LWS_PORT = 8080
DEFAULT_UDP_PORT = 5000

# BrightSign synchronization (roSyncManager) multicast defaults
SYNC_GROUP = "224.0.126.10"
SYNC_PORT = 1539
MAX_SYNC_DATAGRAM = 2048  # larger datagrams are ignored
SYNC_TIMEOUT = 10  # seconds without sync traffic before the group counts as stopped
SIGNAL_SYNC = "brightsign_sync_{}"

# HDMI-CEC payloads sent with the local DWS sendCecX endpoint (source 4 -> TV 0)
CEC_DISPLAY_ON = "4004"  # Image View On
CEC_DISPLAY_STANDBY = "4036"  # Standby

SERVICE_SEND_UDP = "send_udp"
ATTR_HOST = "host"
ATTR_PORT = "port"
ATTR_MESSAGE = "message"
