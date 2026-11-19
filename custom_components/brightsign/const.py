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

# Options
CONF_UDP_PORT = "udp_port"  # where the presentation sends its UDP messages
CONF_SNAPSHOT_INTERVAL = "snapshot_interval"
DEFAULT_SNAPSHOT_INTERVAL = 60  # seconds between screenshots (each one is written to the SD card)

# UDP messages sent by presentations (receive-only listener)
MAX_UDP_EVENT_DATAGRAM = 1024
MAX_UDP_EVENTS_PER_SECOND = 20  # per player; more is dropped
SIGNAL_UDP_EVENT = "brightsign_udp_event_{}"
EVENT_UDP_MESSAGE = "brightsign_udp_message"

STORAGE_POLL_EVERY = 20  # polls (10 minutes)

SERVICE_SEND_UDP = "send_udp"
SERVICE_SEND_COMMAND = "send_command"
ATTR_DEVICE_ID = "device_id"
ATTR_COMMAND = "command"
ATTR_HOST = "host"
ATTR_PORT = "port"
ATTR_MESSAGE = "message"
