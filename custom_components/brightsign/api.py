"""Local network client for BrightSign players.

Uses only documented local entry points:
- the Local Diagnostic Web Server REST API (``/api/v1/...``, digest auth, user ``admin``)
- the Local Web Server XML API of a BrightAuthor presentation (port 8080)
- UDP commands to the presentation's UDP receiver port
- as a read-only fallback for players without the REST API (older BrightSignOS), the Info
  page of the Diagnostic Web Server (name, model, serial, OS, video mode, uptime)

Everything a player sends is treated as untrusted: responses are size limited, text is
sanitized and bounded, and UDP commands are validated before they are sent.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
from collections.abc import Iterable
from dataclasses import dataclass, field
import hashlib
import html
import ipaddress
import json
import logging
import os
import re
import socket
import ssl
from typing import Any
import xml.etree.ElementTree as ET

import aiohttp
from cryptography import x509
from cryptography.x509.oid import NameOID

from .const import DEFAULT_UDP_PORT, DWS_USERNAME, LWS_PORT, REQUEST_TIMEOUT

_LOGGER = logging.getLogger(__name__)

MAX_RESPONSE_BYTES = 1_048_576  # 1 MiB
MAX_IMAGE_BYTES = 16_777_216  # 16 MiB (full resolution snapshots)
MAX_VALUE_LENGTH = 255
_SNAPSHOT_PATH = re.compile(r"/?[a-z0-9]{2,8}/remote_snapshots/[A-Za-z0-9._-]{1,80}\.(?:jpe?g|png)")
_VIDEO_MODE = re.compile(r"[A-Za-z0-9:._+-]{3,48}")
MAX_TEXT_LENGTH = 64
MAX_ITEMS = 64  # user variables / UDP events taken from one presentation
MAX_UDP_MESSAGE = 1024
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_UDP_MESSAGE = re.compile(r"[\x20-\x7e]+")


class BrightSignError(Exception):
    """Base error."""


class BrightSignConnectionError(BrightSignError):
    """The player could not be reached or sent an unusable answer."""


class BrightSignAuthError(BrightSignError):
    """The player rejected the credentials."""


class BrightSignCertificateError(BrightSignAuthError):
    """The player's HTTPS certificate no longer matches the pinned one."""


class BrightSignNotSupported(BrightSignError):
    """The endpoint does not exist on this player (HTTP 404)."""


@dataclass
class ProbeResult:
    """What a player offers on the local network."""

    reachable: bool = False
    rest_scheme: str | None = None
    auth_failed: bool = False
    lws: bool = False
    serial: str | None = None
    model: str | None = None
    name: str | None = None
    firmware: str | None = None
    fingerprint: str | None = None
    legacy: bool | None = False  # None: unknown (player did not answer), detect later
    # Serial-like names from the HTTPS certificate: tried as the default password
    certificate_serials: list[str] = field(default_factory=list)


@dataclass
class LwsData:
    """Data from the presentation's Local Web Server."""

    identity: dict[str, str] = field(default_factory=dict)
    user_vars: dict[str, str] = field(default_factory=dict)
    udp_events: list[dict[str, str]] = field(default_factory=list)
    udp_receive_port: int | None = None
    udp_destination_port: int | None = None


# ---------------------------------------------------------------------------
# Validation and sanitizing
# ---------------------------------------------------------------------------


def sanitize_text(value: Any, max_length: int = MAX_TEXT_LENGTH) -> str:
    """Make device text safe for names and states: no control characters, bounded."""
    text = _CONTROL_CHARS.sub("", str(value)).strip()
    return text[:max_length]


def is_valid_udp_message(message: str) -> bool:
    """Printable ASCII only, 1 to 1024 characters."""
    return 0 < len(message) <= MAX_UDP_MESSAGE and _UDP_MESSAGE.fullmatch(message) is not None


def is_local_address(host: str) -> bool:
    """True for an IP literal that stays on the local network."""
    try:
        address = ipaddress.ip_address(host.strip())
    except ValueError:
        return False
    if address.version == 4 and str(address) == "255.255.255.255":
        return True
    return (
        address.is_private or address.is_link_local or address.is_loopback or address.is_multicast
    )


_SERIAL_LIKE = re.compile(r"(?=[A-Z0-9]*[0-9])[A-Z0-9]{8,16}")  # serials contain digits


def serial_from_name(name: str) -> str | None:
    """Serial number in a host or certificate name ("brightsign-<serial>[.domain]")."""
    label = str(name).strip().split(".")[0]
    if label.lower().startswith("brightsign-"):
        label = label[len("brightsign-") :]
    label = label.upper()
    return label if _SERIAL_LIKE.fullmatch(label) else None


def certificate_serials(der: bytes) -> list[str]:
    """Serial-like names (common name, DNS names) of a DER certificate; at most two."""
    try:
        cert = x509.load_der_x509_certificate(der)
    except ValueError:
        return []
    names = [str(attr.value) for attr in cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)]
    try:
        alt = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    except x509.ExtensionNotFound:
        pass  # no alternative names in this certificate
    else:
        names += alt.value.get_values_for_type(x509.DNSName)
    serials: list[str] = []
    for name in names:
        serial = serial_from_name(name)
        if serial and serial not in serials:
            serials.append(serial)
    return serials[:2]


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def find_key(obj: Any, key: str, _depth: int = 0) -> Any:
    """Return the first value for ``key`` anywhere in a JSON structure (bounded depth)."""
    if _depth > 12:
        return None
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() == key.lower():
                return unwrap(v)
        for v in obj.values():
            found = find_key(v, key, _depth + 1)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj[:256]:
            found = find_key(v, key, _depth + 1)
            if found is not None:
                return found
    return None


def unwrap(value: Any) -> Any:
    """BrightSign nests values as {"result": value} or {"data": {"result": value}}."""
    for _ in range(8):
        if isinstance(value, dict) and len(value) == 1 and next(iter(value)) in ("data", "result"):
            value = next(iter(value.values()))
        else:
            break
    return value


def info_value(info: dict[str, Any], key: str) -> str | None:
    """Return an info field as a short, clean, non-empty string."""
    value = find_key(info, key)
    if value is None or isinstance(value, dict | list):
        return None
    text = sanitize_text(value)
    return text or None


def _parse_challenge(header: str) -> tuple[str, dict[str, str]]:
    match = re.match(r"\s*(\w+)\s+(.*)", header or "")
    if not match:
        return "", {}
    params: dict[str, str] = {}
    for key, quoted, token in re.findall(r'(\w+)\s*=\s*(?:"([^"]*)"|([^,\s]+))', match.group(2)):
        params[key.lower()] = quoted if quoted else token
    return match.group(1).lower(), params


def _hash(algorithm: str, text: str) -> str:
    data = text.encode("utf-8")
    if algorithm == "SHA-256":
        return hashlib.sha256(data).hexdigest()
    # MD5 is what HTTP digest authentication on the player requires.
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def build_digest_header(
    challenges: list[str], method: str, uri: str, username: str, password: str
) -> str | None:
    """Build an RFC 7616 Digest Authorization header (SHA-256 or MD5, qop=auth)."""
    parsed = [_parse_challenge(c) for c in challenges[:8]]
    digests = [p for scheme, p in parsed if scheme == "digest"]
    digests.sort(key=lambda p: 0 if "SHA-256" in p.get("algorithm", "").upper() else 1)
    for params in digests:
        algorithm = params.get("algorithm", "MD5")
        alg = algorithm.upper()
        base = alg.removesuffix("-SESS")
        if base not in ("MD5", "SHA-256"):
            continue
        realm = params.get("realm", "")
        nonce = params.get("nonce", "")
        cnonce = os.urandom(8).hex()
        nc = "00000001"
        ha1 = _hash(base, f"{username}:{realm}:{password}")
        if alg.endswith("-SESS"):
            ha1 = _hash(base, f"{ha1}:{nonce}:{cnonce}")
        ha2 = _hash(base, f"{method}:{uri}")
        qops = [q.strip() for q in params.get("qop", "").split(",") if q.strip()]
        parts = [
            f'username="{username}"',
            f'realm="{realm}"',
            f'nonce="{nonce}"',
            f'uri="{uri}"',
            f"algorithm={algorithm}",
        ]
        if qops:
            qop = "auth" if "auth" in qops else qops[0]
            response = _hash(base, f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
            parts += [f"qop={qop}", f"nc={nc}", f'cnonce="{cnonce}"']
        else:
            response = _hash(base, f"{ha1}:{nonce}:{ha2}")
        parts.append(f'response="{response}"')
        if "opaque" in params:
            parts.append(f'opaque="{params["opaque"]}"')
        return "Digest " + ", ".join(parts)
    return None


def parse_xml(text: str) -> ET.Element | None:
    """Parse a small XML document from the player; None if it is not plain XML."""
    if not text or "<" not in text or len(text) > MAX_RESPONSE_BYTES:
        return None
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        return None
    body = re.sub(r"^\s*<\?xml[^>]*\?>", "", text.strip())
    try:
        return ET.fromstring(body)  # noqa: S314 - DOCTYPE/ENTITY rejected above, size bounded
    except ET.ParseError:
        return None


def xml_fields(root: ET.Element | None) -> dict[str, str]:
    """Flatten the leaf elements of an XML document into a dict."""
    if root is None:
        return {}
    fields: dict[str, str] = {}
    for el in root.iter():
        if len(el) == 0 and el is not root and len(fields) < 256:
            fields[sanitize_text(el.tag)] = sanitize_text(el.text or "", 255)
    return fields


def parse_user_vars(root: ET.Element | None) -> dict[str, str]:
    """Parse GetUserVars XML into {name: value} (bounded)."""
    out: dict[str, str] = {}
    if root is None:
        return out
    for el in root.iter():
        if len(out) >= MAX_ITEMS:
            break
        name = sanitize_text(el.attrib.get("name", "")) if el is not root else ""
        if name:
            out[name] = sanitize_text(el.text or "", 255)
    return out


def parse_udp_events(root: ET.Element | None) -> tuple[list[dict[str, str]], int | None]:
    """Parse GetUDPEvents XML into ([{label, action}], receive_port); invalid actions dropped."""
    events: list[dict[str, str]] = []
    if root is None:
        return events, None
    for el in root.iter():
        if len(events) >= MAX_ITEMS:
            break
        kids = {c.tag.lower(): (c.text or "").strip() for c in el}
        action = kids.get("action", "")
        if action and is_valid_udp_message(action):
            label = sanitize_text(kids.get("label") or action) or sanitize_text(action)
            events.append({"label": label, "action": action})
    return events, _xml_port(root, "receivePort")


def _xml_port(root: ET.Element | None, tag: str) -> int | None:
    port_text = xml_fields(root).get(tag, "")
    return int(port_text) if port_text.isdigit() and 0 < int(port_text) < 65536 else None


# Labels on the Info page of the (pre-REST) Diagnostic Web Server.
_LEGACY_LABELS = {
    "name": "name",
    "description": "description",
    "model": "model",
    "unique id": "serial",
    "boot version": "boot_version",
    "firmware version": "firmware",
    "video mode": "video_mode",
    "uptime": "uptime",
    "poe": "poe",
    "ethernet mac": "mac",
}
_UPTIME_UNITS = {"day": 86400, "hour": 3600, "minute": 60, "second": 1}


def parse_legacy_info(page: str) -> dict[str, str]:
    """Read the label/value pairs of a Diagnostic Web Server Info page (bounded)."""
    if not page or len(page) > MAX_RESPONSE_BYTES or "brightsign" not in page.lower():
        return {}
    text = re.sub(r"<(script|style)\b[^>]*>.*?</\1\s*>", " ", page, flags=re.S | re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "\n", text))
    lines = [line.strip() for line in text.splitlines() if line.strip()][:2000]
    fields: dict[str, str] = {}
    for index, line in enumerate(lines):
        label, _, value = line.partition(":")
        key = _LEGACY_LABELS.get(label.strip().lower())
        if not key or key in fields or not _:
            continue
        if value.strip():  # "Label: value" on one line
            fields[key] = sanitize_text(value, 128)
        elif index + 1 < len(lines):  # "Label:" followed by the value
            following = lines[index + 1]
            if following.partition(":")[0].strip().lower() not in _LEGACY_LABELS:
                fields[key] = sanitize_text(following, 128)
    if "video_mode" in fields:
        fields["video_mode"] = fields["video_mode"].split(" (")[0]
    return fields if ("serial" in fields or "firmware" in fields) else {}


def parse_uptime(text: str | None) -> int | None:
    """'3 hours 3 minutes' -> seconds."""
    found = re.findall(r"(\d{1,7})\s*(day|hour|minute|second)", (text or "").lower())
    if not found:
        return None
    return sum(int(number) * _UPTIME_UNITS[unit] for number, unit in found)


_PNP_VENDORS = {
    "ACR": "Acer",
    "AOC": "AOC",
    "BNQ": "BenQ",
    "DEL": "Dell",
    "EIZ": "EIZO",
    "GSM": "LG",
    "HIS": "Hisense",
    "HSD": "Hisense",
    "HWP": "HP",
    "IVM": "iiyama",
    "LEN": "Lenovo",
    "MEI": "Panasonic",
    "NEC": "NEC",
    "PHL": "Philips",
    "PNS": "Panasonic",
    "SAM": "Samsung",
    "SHP": "Sharp",
    "SNY": "Sony",
    "TCL": "TCL",
    "TOS": "Toshiba",
    "VIZ": "Vizio",
    "VSC": "ViewSonic",
}


def parse_edid(value: Any) -> dict[str, str]:
    """Identify the connected display from an EDID (hex string or already parsed dict)."""
    if isinstance(value, dict):
        out = {}
        for key, target in (
            ("manufacturer", "manufacturer"),
            ("manufacturerName", "manufacturer"),
            ("monitorName", "model"),
            ("productName", "model"),
            ("serialNumber", "serial"),
        ):
            if value.get(key) and target not in out:
                out[target] = sanitize_text(value[key])
        return out
    if not isinstance(value, str):
        return {}
    text = re.sub(r"[^0-9a-fA-F]", "", value)[:2048]
    try:
        raw = bytes.fromhex(text[: len(text) - len(text) % 2])
    except ValueError:
        return {}
    if len(raw) < 128 or raw[:8] != bytes.fromhex("00ffffffffffff00") or not any(raw[8:10]):
        return {}  # power save returns zeroes
    code = (raw[8] << 8) | raw[9]
    pnp = "".join(chr(((code >> shift) & 0x1F) + 64) for shift in (10, 5, 0))
    out = {"manufacturer_id": pnp, "product_code": f"{raw[10] | (raw[11] << 8):04X}"}
    if pnp.isalpha():
        out["manufacturer"] = _PNP_VENDORS.get(pnp, pnp)
    for start in (54, 72, 90, 108):
        block = raw[start : start + 18]
        if block[:3] == b"\x00\x00\x00" and block[3] in (0xFC, 0xFF):
            text_value = block[5:18].split(b"\x0a")[0].decode("ascii", errors="ignore").strip()
            if text_value:
                out["model" if block[3] == 0xFC else "serial"] = sanitize_text(text_value)
    return out


def parse_video_modes(value: Any) -> list[str]:
    """Video mode names from a modes response (bounded, validated)."""
    items = value if isinstance(value, list) else find_key(value, "modes")
    if not isinstance(items, list):
        return []
    modes: list[str] = []
    for item in items[:256]:
        name = item.get("modeName") or item.get("name") if isinstance(item, dict) else item
        if isinstance(name, str) and _VIDEO_MODE.fullmatch(name) and name not in modes:
            modes.append(name)
    return modes[:MAX_ITEMS]


async def async_send_udp(host: str, port: int, message: str) -> None:
    """Send one validated UDP datagram (broadcast addresses allowed)."""
    if not is_valid_udp_message(message):
        raise BrightSignError("UDP message must be 1-1024 printable ASCII characters")
    if not 0 < port < 65536:
        raise BrightSignError(f"invalid UDP port {port}")
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setblocking(False)
        await loop.sock_sendto(sock, message.encode("ascii"), (host, port))
    except OSError as err:
        raise BrightSignConnectionError(f"UDP send to {host}:{port} failed: {err}") from err
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class BrightSignClient:
    """Talks to one BrightSign player."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        password: str = "",
        rest_scheme: str | None = None,
        fingerprint: str | None = None,
        no_verify_context: ssl.SSLContext | None = None,
    ) -> None:
        self.host = host
        self._session = session
        self._password = password or ""
        self.rest_scheme = rest_scheme
        self.fingerprint = fingerprint
        self._no_verify_context = no_verify_context
        self._snapshot_delete: bool | None = None  # None: not tried yet

    def _ssl(self) -> aiohttp.Fingerprint | bool:
        """Pinned certificate when known; the player's certificate is self-signed."""
        if self.rest_scheme == "https" and self.fingerprint:
            return aiohttp.Fingerprint(bytes.fromhex(self.fingerprint))
        return False

    async def _read_limited(
        self, resp: aiohttp.ClientResponse, limit: int = MAX_RESPONSE_BYTES
    ) -> bytes:
        if resp.content_length is not None and resp.content_length > limit:
            raise BrightSignConnectionError("response too large")
        # read() with a size returns as soon as some data arrived: read on until the end
        body = bytearray()
        async for chunk in resp.content.iter_chunked(65536):
            body += chunk
            if len(body) > limit:
                raise BrightSignConnectionError("response too large")
        return bytes(body)

    # ----- HTTP with digest authentication -------------------------------

    async def _http(
        self,
        method: str,
        url: str,
        path: str,
        payload: dict | None = None,
        accept: str = "application/json",
        ssl_param: aiohttp.Fingerprint | bool = False,
        limit: int = MAX_RESPONSE_BYTES,
    ) -> bytes:
        # Older players announce keep-alive but drop the connection: never reuse it.
        headers = {"Accept": accept, "Connection": "close"}
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                resp = await self._session.request(
                    method, url, json=payload, headers=headers, ssl=ssl_param
                )
                if resp.status == 401:
                    challenges = resp.headers.getall("WWW-Authenticate", [])
                    resp.release()
                    auth = build_digest_header(
                        challenges, method, resp.url.path_qs, DWS_USERNAME, self._password
                    )
                    if auth is None:
                        raise BrightSignAuthError("unsupported authentication scheme")
                    resp = await self._session.request(
                        method,
                        url,
                        json=payload,
                        headers={**headers, "Authorization": auth},
                        ssl=ssl_param,
                    )
                async with resp:
                    if resp.status == 401:
                        raise BrightSignAuthError("authentication failed")
                    if resp.status == 404:
                        raise BrightSignNotSupported(path)
                    if resp.status >= 400:
                        detail = sanitize_text(
                            (await resp.content.read(300)).decode("utf-8", errors="replace"), 200
                        )
                        _LOGGER.debug(
                            "%s %s on %s: HTTP %s %s", method, path, self.host, resp.status, detail
                        )
                        raise BrightSignConnectionError(f"HTTP {resp.status} for {path}")
                    return await self._read_limited(resp, limit)
        except aiohttp.ServerFingerprintMismatch as err:
            raise BrightSignCertificateError("HTTPS certificate changed") from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise BrightSignConnectionError(f"{path}: {type(err).__name__}") from err

    # ----- REST (local DWS) ------------------------------------------------

    async def _rest(self, method: str, path: str, payload: dict | None = None) -> Any:
        if self.rest_scheme is None:
            raise BrightSignNotSupported("no REST API")
        url = f"{self.rest_scheme}://{self.host}{path}"
        body = await self._http(method, url, path, payload, ssl_param=self._ssl())
        if not body.strip():
            return {}
        try:
            return unwrap(json.loads(body))
        except (ValueError, RecursionError) as err:
            raise BrightSignConnectionError(f"{path}: invalid JSON") from err

    async def async_get_info(self) -> dict[str, Any]:
        """GET /api/v1/info/."""
        data = await self._rest("GET", "/api/v1/info/")
        return data if isinstance(data, dict) else {}

    async def async_get_video_output(self) -> dict[str, Any]:
        """GET /api/v1/video/hdmi/output/0/."""
        data = await self._rest("GET", "/api/v1/video/hdmi/output/0/")
        return data if isinstance(data, dict) else {}

    async def async_get_power_save(self) -> bool | None:
        """True when the HDMI output is in power-save (switched off)."""
        data = await self._rest("GET", "/api/v1/video/hdmi/output/0/power-save/")
        value = find_key(data, "enabled") if isinstance(data, dict) else data
        return value if isinstance(value, bool) else None

    async def async_set_hdmi_output(self, on: bool) -> None:
        """Switch the HDMI output on/off (power-save)."""
        await self._rest("PUT", "/api/v1/video/hdmi/output/0/power-save/", {"enabled": not on})

    async def async_reboot(self) -> None:
        """Reboot the player."""
        await self._rest("PUT", "/api/v1/control/reboot/")

    async def async_send_cec(self, hex_command: str) -> None:
        """Send a raw HDMI-CEC payload out of HDMI-1 (fixed payloads only)."""
        if not re.fullmatch(r"(?:[0-9a-f]{2}){2,16}", hex_command):
            raise BrightSignError("invalid CEC payload")
        try:
            await self._rest("POST", "/api/v1/sendCecX/", {"hexCommand": hex_command})
        except BrightSignNotSupported as err:
            raise BrightSignNotSupported(
                "the player could not send the CEC command (no display with CEC connected, "
                "or a BrightSignOS without CEC commands in the DWS)"
            ) from err

    async def async_get_video_modes(self) -> list[str]:
        """Video modes the connected display supports."""
        return parse_video_modes(await self._rest("GET", "/api/v1/video/hdmi/output/0/modes/"))

    async def async_set_video_mode(self, mode: str) -> None:
        """Change the video mode (until the presentation sets its own mode again)."""
        if not _VIDEO_MODE.fullmatch(mode):
            raise BrightSignError("invalid video mode")
        path = "/api/v1/video/hdmi/output/0/mode/"
        try:
            # The form of BrightSign's own example; players reject a bare {"modeName": ...}
            await self._rest("PUT", path, {"name": {"modeName": mode}})
        except BrightSignConnectionError as err:
            if not str(err).startswith("HTTP 500"):
                raise
            await self._rest("PUT", path, {"modeName": mode})

    async def async_get_edid(self) -> dict[str, str]:
        """Identify the connected display."""
        data = await self._rest("GET", "/api/v1/video/hdmi/output/0/edid/")
        return parse_edid(data if not isinstance(data, dict) else (find_key(data, "edid") or data))

    async def async_get_storage(self) -> dict[str, int]:
        """Free and total bytes of the SD card."""
        data = await self._rest("GET", "/api/v1/files/sd/")
        out: dict[str, int] = {}
        for key, target in (("bytesFree", "free"), ("sizeBytes", "size")):
            value = find_key(data, key)
            if isinstance(value, int) and value >= 0:
                out[target] = value
        return out

    async def async_send_custom(self, command: str) -> None:
        """Send custom data to the presentation (delivered by the player on UDP port 5000)."""
        if not is_valid_udp_message(command):
            raise BrightSignError("command must be 1-1024 printable ASCII characters")
        await self._rest("PUT", "/api/v1/custom/", {"command": command, "returnImmediately": True})

    async def async_snapshot(self) -> bytes | None:
        """Screenshot of what the player shows (JPEG). The file is removed afterwards."""
        data = await self._rest("POST", "/api/v1/snapshot/", {})
        filename = find_key(data, "filename")
        image: bytes | None = None
        if isinstance(filename, str) and _SNAPSHOT_PATH.fullmatch(filename):
            path = "/api/v1/files/" + filename.lstrip("/")
            image = await self._async_download_image(path)
            for _attempt in range(2):
                if image and not (
                    image.startswith(b"\xff\xd8")
                    and not image.rstrip(b"\x00").endswith(b"\xff\xd9")
                ):
                    break  # complete
                await asyncio.sleep(1)  # the player is still writing the file
                image = await self._async_download_image(path) or image
            await self._async_remove_snapshot(path, downloaded=image is not None)
        if not image:
            thumbnail = find_key(data, "remoteSnapshotThumbnail")
            if isinstance(thumbnail, str) and "," in thumbnail:
                try:
                    image = base64.b64decode(thumbnail.split(",", 1)[1], validate=True)
                except (binascii.Error, ValueError):
                    image = None
        if image and not image.startswith((b"\xff\xd8", b"\x89PNG")):
            return None
        return image

    async def _async_download_image(self, path: str) -> bytes | None:
        try:
            return await self._http(
                "GET",
                f"{self.rest_scheme}://{self.host}{path}?contents&stream",
                path,
                accept="image/*",
                ssl_param=self._ssl(),
                limit=MAX_IMAGE_BYTES,
            )
        except BrightSignNotSupported:
            _LOGGER.debug("Snapshot %s on %s is not there (yet)", path, self.host)
            return None
        except BrightSignError as err:
            _LOGGER.debug("Could not download snapshot %s from %s: %s", path, self.host, err)
            return None

    async def _async_remove_snapshot(self, path: str, downloaded: bool = True) -> None:
        """Remove a snapshot from the SD card (players that allow it)."""
        if self._snapshot_delete is False:
            return
        for candidate in (path, path + "/"):
            try:
                await self._rest("DELETE", candidate)
            except BrightSignNotSupported:
                continue
            except BrightSignError as err:
                _LOGGER.debug("Could not remove snapshot %s from %s: %s", path, self.host, err)
                return
            self._snapshot_delete = True
            return
        if not downloaded:
            return  # the file may not exist: try again with the next snapshot
        if self._snapshot_delete is None:
            _LOGGER.debug(
                "%s does not allow removing snapshots; it keeps its own number of snapshots",
                self.host,
            )
        self._snapshot_delete = False

    async def async_get_legacy_info(self) -> dict[str, str]:
        """Read-only fallback: the Info page of a pre-REST Diagnostic Web Server."""
        try:
            body = await self._http(
                "GET", f"http://{self.host}/index.html", "/index.html", accept="text/html"
            )
        except BrightSignNotSupported:
            return {}
        return parse_legacy_info(body.decode("utf-8", errors="replace"))

    async def async_fetch_certificate(self) -> bytes | None:
        """The player's (self-signed) HTTPS certificate, DER encoded."""
        if self._no_verify_context is None:
            return None
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                _reader, writer = await asyncio.open_connection(
                    self.host, 443, ssl=self._no_verify_context
                )
        except (OSError, TimeoutError):
            return None
        try:
            ssl_object = writer.get_extra_info("ssl_object")
            return ssl_object.getpeercert(binary_form=True) if ssl_object else None
        finally:
            writer.close()

    async def async_fetch_fingerprint(self) -> str | None:
        """SHA-256 fingerprint of the player's HTTPS certificate (for pinning)."""
        der = await self.async_fetch_certificate()
        return hashlib.sha256(der).hexdigest() if der else None

    async def async_get_unit_name(self) -> str | None:
        """Unit name set on the player (registry networking/un), if any."""
        data = await self._rest("GET", "/api/v1/registry/networking/un/")
        value = find_key(data, "value")
        if not isinstance(value, str):
            return None
        return sanitize_text(value) or None

    # ----- Local Web Server (presentation) ---------------------------------

    async def _lws_get(self, path: str) -> ET.Element | None:
        url = f"http://{self.host}:{LWS_PORT}{path}"
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.get(url, headers={"Connection": "close"}) as resp:
                    if resp.status != 200:
                        return None
                    body = await self._read_limited(resp)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise BrightSignConnectionError(f"LWS {path}: {type(err).__name__}") from err
        return parse_xml(body.decode("utf-8", errors="replace"))

    async def async_get_lws(self) -> LwsData:
        """Read identity, user variables and UDP events from the presentation."""
        data = LwsData()
        data.identity = xml_fields(await self._lws_get("/GetID"))
        data.user_vars = parse_user_vars(await self._lws_get("/GetUserVars"))
        udp_root = await self._lws_get("/GetUDPEvents")
        data.udp_events, data.udp_receive_port = parse_udp_events(udp_root)
        data.udp_destination_port = _xml_port(udp_root, "destinationPort")
        return data

    async def async_set_user_variable(self, name: str, value: str) -> None:
        """Change a user variable of the presentation (Local Web Server /SetValues)."""
        if not sanitize_text(name) or sanitize_text(name) != name:
            raise BrightSignError("invalid user variable name")
        if len(value) > MAX_VALUE_LENGTH or _CONTROL_CHARS.search(value):
            raise BrightSignError("value must be at most 255 characters, no control characters")
        for port in (8008, LWS_PORT):  # user variable page first, then the main web server
            url = f"http://{self.host}:{port}/SetValues"
            try:
                async with asyncio.timeout(REQUEST_TIMEOUT):
                    async with self._session.post(
                        url, data={name: value}, headers={"Connection": "close"}
                    ) as resp:
                        if resp.status == 404:
                            continue
                        if resp.status >= 400:
                            raise BrightSignConnectionError(f"SetValues: HTTP {resp.status}")
                        await self._read_limited(resp)
                        return
            except (aiohttp.ClientError, TimeoutError):
                continue
        raise BrightSignConnectionError("the presentation does not accept user variable changes")

    async def async_send_udp_command(self, action: str, port: int | None) -> None:
        """Send a UDP command to this player's UDP receiver port."""
        await async_send_udp(self.host, port or DEFAULT_UDP_PORT, action)

    # ----- Reachability / probe ----------------------------------------------

    async def async_reachable(self) -> bool:
        """True when any of the player's TCP service ports answers (checked in parallel)."""

        async def _check(port: int) -> bool:
            try:
                async with asyncio.timeout(2):
                    _reader, writer = await asyncio.open_connection(self.host, port)
            except (OSError, TimeoutError):
                return False
            writer.close()
            return True

        results = await asyncio.gather(*(_check(port) for port in (80, 443, LWS_PORT)))
        return any(results)

    async def async_probe(self) -> ProbeResult:
        """Detect which local interfaces the player offers (first contact, no pin yet)."""
        result = ProbeResult()
        self.fingerprint = None
        for scheme in ("https", "http"):
            self.rest_scheme = scheme
            try:
                info = await self.async_get_info()
            except BrightSignAuthError:
                result.reachable = True
                result.rest_scheme = scheme
                result.auth_failed = True
                break
            except BrightSignNotSupported:
                result.reachable = True
                continue
            except BrightSignConnectionError:
                continue
            result.reachable = True
            result.rest_scheme = scheme
            result.serial = info_value(info, "serial")
            result.model = info_value(info, "model")
            result.firmware = info_value(info, "FWVersion")
            result.name = info_value(info, "deviceName") or info_value(info, "name")
            break
        if result.rest_scheme is None and result.reachable:
            try:
                legacy = await self.async_get_legacy_info()
            except BrightSignAuthError:
                result.auth_failed = True
                legacy = {}
            except BrightSignConnectionError:
                result.legacy = None  # unknown: the coordinator detects it later
                legacy = {}
            if legacy:
                result.legacy = True
                result.serial = legacy.get("serial")
                result.model = legacy.get("model")
                result.firmware = legacy.get("firmware")
                result.name = legacy.get("name")
        if result.rest_scheme == "https":
            der = await self.async_fetch_certificate()
            if der:
                result.certificate_serials = certificate_serials(der)
                if not result.auth_failed:
                    result.fingerprint = hashlib.sha256(der).hexdigest()
        self.rest_scheme = None if result.auth_failed else result.rest_scheme
        self.fingerprint = result.fingerprint

        try:
            lws = await self.async_get_lws()
        except BrightSignConnectionError:
            lws = None
        if lws is not None and lws.identity:
            result.reachable = True
            result.lws = True
            result.serial = result.serial or lws.identity.get("serialNumber") or None
            result.name = result.name or lws.identity.get("unitName") or None
            result.firmware = result.firmware or lws.identity.get("firmwareVersion") or None

        if not result.reachable:
            result.reachable = await self.async_reachable()
        _LOGGER.debug(
            "Probe of %s: reachable=%s, REST=%s, password needed=%s, Info page=%s, "
            "web server=%s, serial known=%s, certificate names=%d",
            self.host,
            result.reachable,
            result.rest_scheme,
            result.auth_failed,
            result.legacy,
            result.lws,
            bool(result.serial),
            len(result.certificate_serials),
        )
        return result


# ---------------------------------------------------------------------------
# Network scan (only when the user opens "Add integration")
# ---------------------------------------------------------------------------

BRIGHTSIGN_OUIS = ("90:ac:3f",)  # IEEE OUI of BrightSign LLC
MAX_SCAN_HOSTS = 1024
SCAN_CONCURRENCY = 128
SCAN_TIMEOUT = 0.8
ARP_TABLE = "/proc/net/arp"
_ARP_COMPLETE = 0x2
_MAC_ADDRESS = re.compile(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}")


def is_brightsign_mac(mac: str | None) -> bool:
    """True for a MAC address of a BrightSign player."""
    return bool(mac) and str(mac).lower()[:8] in BRIGHTSIGN_OUIS


def scan_targets(interfaces: Iterable[str], limit: int = MAX_SCAN_HOSTS) -> list[str]:
    """Addresses to scan, from Home Assistant's own interfaces ("address/prefix").

    Only private IPv4 networks. A network larger than /22 is narrowed down to the /24
    Home Assistant itself is in.
    """
    targets: list[str] = []
    seen: set[str] = set()
    for value in interfaces:
        try:
            interface = ipaddress.IPv4Interface(value)
        except ValueError:
            continue
        network = interface.network
        if not network.is_private or network.is_loopback or network.is_link_local:
            continue
        if network.num_addresses > MAX_SCAN_HOSTS:
            network = ipaddress.IPv4Interface(f"{interface.ip}/24").network
        for host in network.hosts():
            address = str(host)
            if host == interface.ip or address in seen:
                continue
            seen.add(address)
            targets.append(address)
            if len(targets) >= limit:
                return targets
    return targets


def parse_arp_table(text: str) -> dict[str, str]:
    """IP address -> MAC address of the complete entries of a Linux ARP table."""
    table: dict[str, str] = {}
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        address, flags, mac = parts[0], parts[2], parts[3].lower()
        try:
            complete = int(flags, 16) & _ARP_COMPLETE
            ipaddress.IPv4Address(address)
        except ValueError:
            continue
        if complete and _MAC_ADDRESS.fullmatch(mac) and mac != "00:00:00:00:00:00":
            table[address] = mac
    return table


def _read_arp_table() -> str:
    try:
        with open(ARP_TABLE, encoding="ascii", errors="replace") as file:
            return file.read(MAX_RESPONSE_BYTES)
    except OSError:
        return ""  # not Linux, or no access: the scan finds nothing


async def async_scan_for_players(targets: Iterable[str]) -> dict[str, str]:
    """BrightSign players on the local network: IP address -> MAC address.

    Knocks once on TCP port 80 of every target (closed right away, nothing is sent),
    so the operating system learns their MAC addresses, then keeps the addresses of
    BrightSign players from the ARP table.
    """
    semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)

    async def _knock(host: str) -> None:
        async with semaphore:
            try:
                async with asyncio.timeout(SCAN_TIMEOUT):
                    _reader, writer = await asyncio.open_connection(host, 80)
            except (OSError, TimeoutError):
                return
            writer.close()

    await asyncio.gather(*(_knock(host) for host in targets if is_local_address(host)))
    table = parse_arp_table(await asyncio.to_thread(_read_arp_table))
    players = {
        address: mac
        for address, mac in table.items()
        if is_brightsign_mac(mac) and is_local_address(address)
    }
    return dict(sorted(players.items(), key=lambda item: ipaddress.IPv4Address(item[0])))
