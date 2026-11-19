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
CONF_HOSTNAME = "hostname"  # DHCP host name (network name)
CONF_ADOPTED_NAME = "adopted_name"  # name the integration gave the entry (not the user)

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

# PTP (IEEE 1588) clock synchronization of sync groups: leader = PTP master, followers send
# delay requests. Listened to passively to tell leaders and followers apart.
PTP_GROUP = "224.0.1.129"
PTP_PORTS = (319, 320)
MAX_PTP_DATAGRAM = 512
# Ethernet sync scripts (e.g. based on Zach Poff's): the master broadcasts short commands
# ("pre", "ply", ...) to UDP 11167 when an item (re)starts. Listened to passively.
UDP_SYNC_PORT = 11167
MAX_UDP_SYNC_DATAGRAM = 64
UDP_SYNC_TIMEOUT = 600  # the master only sends when its video loops: remember it 10 minutes
SYNC_CHECK_INTERVAL = timedelta(seconds=5)
SYNC_CONFLICT_CHECKS = 6  # a conflict must last this many checks (30 s) before it is reported
SIGNAL_SYNC_ROLE = "brightsign_sync_role_{}"
SYNC_ROLE_LEADER = "leader"
SYNC_ROLE_FOLLOWER = "follower"
SYNC_ROLE_NONE = "none"

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
REDETECT_EVERY = 10  # polls (5 minutes): look again for a web server or Info page

SERVICE_SEND_UDP = "send_udp"
SERVICE_SEND_COMMAND = "send_command"
ATTR_DEVICE_ID = "device_id"
ATTR_COMMAND = "command"
ATTR_HOST = "host"
ATTR_PORT = "port"
ATTR_MESSAGE = "message"
