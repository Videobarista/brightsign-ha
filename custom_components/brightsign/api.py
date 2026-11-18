"""Local network client for BrightSign players.

Uses only documented local entry points:
- the Local Diagnostic Web Server REST API (``/api/v1/...``, digest auth, user ``admin``)
- the Local Web Server XML API of a BrightAuthor presentation (port 8080)
- UDP commands to the presentation's UDP receiver port

Everything a player sends is treated as untrusted: responses are size limited, text is
sanitized and bounded, and UDP commands are validated before they are sent.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import ipaddress
import json
import os
import re
import socket
import ssl
from typing import Any
import xml.etree.ElementTree as ET

import aiohttp

from .const import DEFAULT_UDP_PORT, DWS_USERNAME, LWS_PORT, REQUEST_TIMEOUT

MAX_RESPONSE_BYTES = 1_048_576  # 1 MiB
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


@dataclass
class LwsData:
    """Data from the presentation's Local Web Server."""

    identity: dict[str, str] = field(default_factory=dict)
    user_vars: dict[str, str] = field(default_factory=dict)
    udp_events: list[dict[str, str]] = field(default_factory=list)
    udp_receive_port: int | None = None


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
    port_text = xml_fields(root).get("receivePort", "")
    port = int(port_text) if port_text.isdigit() and 0 < int(port_text) < 65536 else None
    return events, port


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

    def _ssl(self) -> aiohttp.Fingerprint | bool:
        """Pinned certificate when known; the player's certificate is self-signed."""
        if self.rest_scheme == "https" and self.fingerprint:
            return aiohttp.Fingerprint(bytes.fromhex(self.fingerprint))
        return False

    async def _read_limited(self, resp: aiohttp.ClientResponse) -> bytes:
        if resp.content_length is not None and resp.content_length > MAX_RESPONSE_BYTES:
            raise BrightSignConnectionError("response too large")
        body = await resp.content.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise BrightSignConnectionError("response too large")
        return body

    # ----- REST (local DWS) ------------------------------------------------

    async def _rest(self, method: str, path: str, payload: dict | None = None) -> Any:
        if self.rest_scheme is None:
            raise BrightSignNotSupported("no REST API")
        url = f"{self.rest_scheme}://{self.host}{path}"
        headers = {"Accept": "application/json"}
        ssl_param = self._ssl()
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
                        raise BrightSignConnectionError(f"HTTP {resp.status} for {path}")
                    body = await self._read_limited(resp)
        except aiohttp.ServerFingerprintMismatch as err:
            raise BrightSignCertificateError("HTTPS certificate changed") from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise BrightSignConnectionError(f"{path}: {type(err).__name__}") from err
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
        await self._rest("POST", "/api/v1/sendCecX/", {"hexCommand": hex_command})

    async def async_fetch_fingerprint(self) -> str | None:
        """SHA-256 fingerprint of the player's HTTPS certificate (for pinning)."""
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
            der = ssl_object.getpeercert(binary_form=True) if ssl_object else None
        finally:
            writer.close()
        return hashlib.sha256(der).hexdigest() if der else None

    # ----- Local Web Server (presentation) ---------------------------------

    async def _lws_get(self, path: str) -> ET.Element | None:
        url = f"http://{self.host}:{LWS_PORT}{path}"
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.get(url) as resp:
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
        data.udp_events, data.udp_receive_port = parse_udp_events(
            await self._lws_get("/GetUDPEvents")
        )
        return data

    async def async_send_udp_command(self, action: str, port: int | None) -> None:
        """Send a UDP command to this player's UDP receiver port."""
        await async_send_udp(self.host, port or DEFAULT_UDP_PORT, action)

    # ----- Reachability / probe ----------------------------------------------

    async def async_reachable(self) -> bool:
        """True when any of the player's TCP service ports answers."""
        for port in (80, 443, LWS_PORT):
            try:
                async with asyncio.timeout(3):
                    _reader, writer = await asyncio.open_connection(self.host, port)
            except (OSError, TimeoutError):
                continue
            writer.close()
            return True
        return False

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
        if result.rest_scheme == "https" and not result.auth_failed:
            result.fingerprint = await self.async_fetch_fingerprint()
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
        return result
