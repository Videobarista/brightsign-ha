"""Local network client for BrightSign players.

Uses only documented local entry points:
- the Local Diagnostic Web Server REST API (``/api/v1/...``, digest auth, user ``admin``)
- the Local Web Server XML API of a BrightAuthor presentation (port 8080)
- UDP commands to the presentation's UDP receiver port
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import os
import re
import socket
from typing import Any
import xml.etree.ElementTree as ET

import aiohttp

from .const import DEFAULT_UDP_PORT, DWS_USERNAME, LWS_PORT, REQUEST_TIMEOUT

MAX_XML_BYTES = 256_000


class BrightSignError(Exception):
    """Base error."""


class BrightSignConnectionError(BrightSignError):
    """The player could not be reached."""


class BrightSignAuthError(BrightSignError):
    """The player rejected the credentials."""


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


@dataclass
class LwsData:
    """Data from the presentation's Local Web Server."""

    identity: dict[str, str] = field(default_factory=dict)
    user_vars: dict[str, str] = field(default_factory=dict)
    udp_events: list[dict[str, str]] = field(default_factory=list)
    udp_receive_port: int | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def find_key(obj: Any, key: str) -> Any:
    """Return the first value for ``key`` anywhere in a JSON structure."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() == key.lower():
                return unwrap(v)
        for v in obj.values():
            found = find_key(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find_key(v, key)
            if found is not None:
                return found
    return None


def unwrap(value: Any) -> Any:
    """BrightSign nests values as {"result": value} or {"data": {"result": value}}."""
    while isinstance(value, dict) and len(value) == 1 and next(iter(value)) in ("data", "result"):
        value = next(iter(value.values()))
    return value


def _parse_challenge(header: str) -> tuple[str, dict[str, str]]:
    match = re.match(r"\s*(\w+)\s+(.*)", header or "")
    if not match:
        return "", {}
    params: dict[str, str] = {}
    for key, quoted, token in re.findall(r'(\w+)\s*=\s*(?:"([^"]*)"|([^,\s]+))', match.group(2)):
        params[key.lower()] = quoted if quoted else token
    return match.group(1).lower(), params


def build_digest_header(
    challenges: list[str], method: str, uri: str, username: str, password: str
) -> str | None:
    """Build an RFC 7616 Digest Authorization header (MD5 or SHA-256, qop=auth)."""
    parsed = [_parse_challenge(c) for c in challenges]
    digests = [p for scheme, p in parsed if scheme == "digest"]
    digests.sort(key=lambda p: 0 if "SHA-256" in p.get("algorithm", "").upper() else 1)
    hashers = {"MD5": hashlib.md5, "SHA-256": hashlib.sha256}
    for params in digests:
        algorithm = params.get("algorithm", "MD5")
        alg = algorithm.upper()
        base = alg.removesuffix("-SESS")
        if base not in hashers:
            continue
        hasher = hashers[base]

        def digest(text: str, hasher=hasher) -> str:
            return hasher(text.encode("utf-8")).hexdigest()

        realm = params.get("realm", "")
        nonce = params.get("nonce", "")
        cnonce = os.urandom(8).hex()
        nc = "00000001"
        ha1 = digest(f"{username}:{realm}:{password}")
        if alg.endswith("-SESS"):
            ha1 = digest(f"{ha1}:{nonce}:{cnonce}")
        ha2 = digest(f"{method}:{uri}")
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
            response = digest(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
            parts += [f"qop={qop}", f"nc={nc}", f'cnonce="{cnonce}"']
        else:
            response = digest(f"{ha1}:{nonce}:{ha2}")
        parts.append(f'response="{response}"')
        if "opaque" in params:
            parts.append(f'opaque="{params["opaque"]}"')
        return "Digest " + ", ".join(parts)
    return None


def parse_xml(text: str) -> ET.Element | None:
    """Parse a small XML document from the player; None if it is not XML."""
    if not text or "<" not in text or len(text) > MAX_XML_BYTES or "<!DOCTYPE" in text.upper():
        return None
    body = re.sub(r"^\s*<\?xml[^>]*\?>", "", text.strip())
    try:
        return ET.fromstring(body)  # noqa: S314 - local device, DOCTYPE rejected above
    except ET.ParseError:
        return None


def xml_fields(root: ET.Element | None) -> dict[str, str]:
    """Flatten the leaf elements of an XML document into a dict."""
    if root is None:
        return {}
    return {
        el.tag: (el.text or "").strip() for el in root.iter() if len(el) == 0 and el is not root
    }


def parse_user_vars(root: ET.Element | None) -> dict[str, str]:
    """Parse GetUserVars XML into {name: value}."""
    out: dict[str, str] = {}
    if root is None:
        return out
    for el in root.iter():
        if el is not root and el.attrib.get("name"):
            out[el.attrib["name"]] = (el.text or "").strip()
    return out


def parse_udp_events(root: ET.Element | None) -> tuple[list[dict[str, str]], int | None]:
    """Parse GetUDPEvents XML into ([{label, action}], receive_port)."""
    events: list[dict[str, str]] = []
    if root is None:
        return events, None
    for el in root.iter():
        kids = {c.tag.lower(): (c.text or "").strip() for c in el}
        if "action" in kids and kids["action"]:
            events.append({"label": kids.get("label") or kids["action"], "action": kids["action"]})
    port_text = xml_fields(root).get("receivePort", "")
    return events, int(port_text) if port_text.isdigit() else None


async def async_send_udp(host: str, port: int, message: str) -> None:
    """Send one UDP datagram (broadcast addresses allowed)."""
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setblocking(False)
        await loop.sock_sendto(sock, message.encode("utf-8"), (host, port))
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
    ) -> None:
        self.host = host
        self._session = session
        self._password = password or ""
        self.rest_scheme = rest_scheme

    # ----- REST (local DWS) ------------------------------------------------

    async def _rest(self, method: str, path: str, payload: dict | None = None) -> Any:
        if self.rest_scheme is None:
            raise BrightSignNotSupported("no REST API")
        url = f"{self.rest_scheme}://{self.host}{path}"
        headers = {"Accept": "application/json"}
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                resp = await self._session.request(
                    method, url, json=payload, headers=headers, ssl=False
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
                        ssl=False,
                    )
                async with resp:
                    if resp.status == 401:
                        raise BrightSignAuthError("authentication failed")
                    if resp.status == 404:
                        raise BrightSignNotSupported(path)
                    if resp.status >= 400:
                        raise BrightSignConnectionError(f"HTTP {resp.status} for {path}")
                    data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise BrightSignConnectionError(f"{path}: {err}") from err
        return unwrap(data)

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
        """Send a raw HDMI-CEC payload out of HDMI-1."""
        await self._rest("POST", "/api/v1/sendCecX/", {"hexCommand": hex_command})

    # ----- Local Web Server (presentation) ---------------------------------

    async def _lws_get(self, path: str) -> ET.Element | None:
        url = f"http://{self.host}:{LWS_PORT}{path}"
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.get(url) as resp:
                    if resp.status != 200:
                        return None
                    text = await resp.text(errors="replace")
        except (aiohttp.ClientError, TimeoutError) as err:
            raise BrightSignConnectionError(f"LWS {path}: {err}") from err
        return parse_xml(text)

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
        """Detect which local interfaces the player offers."""
        result = ProbeResult()
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
        self.rest_scheme = None if result.auth_failed else result.rest_scheme

        try:
            lws = await self.async_get_lws()
        except BrightSignConnectionError:
            lws = None
        if lws is not None and lws.identity:
            result.reachable = True
            result.lws = True
            result.serial = result.serial or lws.identity.get("serialNumber")
            result.name = result.name or lws.identity.get("unitName")
            result.firmware = result.firmware or lws.identity.get("firmwareVersion")

        if not result.reachable:
            result.reachable = await self.async_reachable()
        return result


def info_value(info: dict[str, Any], key: str) -> str | None:
    """Return an info field as a non-empty string."""
    value = find_key(info, key)
    if value is None or isinstance(value, dict | list):
        return None
    text = str(value).strip()
    return text or None
