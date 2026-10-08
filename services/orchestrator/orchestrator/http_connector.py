"""Pinned-IP HTTPS GET for one operator-configured origin.

Workflow JSON supplies only a relative path and scalar field names. The worker
resolves the configured hostname once, rejects unsafe answers, and connects to
the selected address while verifying TLS against the original hostname.
"""

import asyncio
import ipaddress
import json
import math
import socket
import ssl
from typing import Any
from urllib.parse import urlsplit

import httpx
from app.config import Settings, get_settings
from app.graph import HttpConfig

MAX_RESPONSE_BYTES = 8192
TEST_PRIVATE_RANGES = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


class HttpActionError(Exception):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def allowed_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address, host: str, settings: Settings
) -> bool:
    if address.is_global and not address.is_multicast and not address.is_reserved:
        return True
    return bool(
        settings.app_env == "test"
        and settings.http_connector_test_private_host == host
        and isinstance(address, ipaddress.IPv4Address)
        and any(address in network for network in TEST_PRIVATE_RANGES)
    )


async def pinned_address(host: str, port: int, settings: Settings) -> str:
    try:
        answers = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
        )
    except OSError:
        raise HttpActionError("dns_unavailable", retryable=True) from None
    if not answers:
        raise HttpActionError("dns_unavailable", retryable=True)
    addresses: list[str] = []
    for answer in answers:
        raw = answer[4][0]
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            raise HttpActionError("unsafe_destination") from None
        if not allowed_address(address, host, settings):
            # A mixed public/private DNS answer is rejected in full.
            raise HttpActionError("unsafe_destination")
        addresses.append(str(address))
    return addresses[0]


async def execute_https_get(config: HttpConfig) -> dict[str, Any]:
    settings = get_settings()
    if not settings.http_connector_enabled or config.operation != "https_get" or not config.path:
        raise HttpActionError("connector_disabled")
    origin = urlsplit(settings.http_connector_origin)
    host = origin.hostname
    if host is None:
        raise HttpActionError("connector_configuration")
    port = origin.port or 443
    try:
        context = ssl.create_default_context(cafile=settings.http_connector_test_ca_file or None)
    except OSError:
        raise HttpActionError("connector_configuration") from None
    try:
        async with asyncio.timeout(max(0.5, config.timeout_seconds - 0.25)):
            address = await pinned_address(host, port, settings)
            authority = f"[{address}]" if ":" in address else address
            url = f"https://{authority}:{port}{config.path}"
            host_header = host if port == 443 else f"{host}:{port}"
            async with httpx.AsyncClient(
                verify=context,
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(5.0, connect=3.0),
            ) as client:
                async with client.stream(
                    "GET",
                    url,
                    headers={
                        "Host": host_header,
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                        "Connection": "close",
                    },
                    extensions={"sni_hostname": host},
                ) as response:
                    if response.status_code == 429 or response.status_code >= 500:
                        raise HttpActionError("http_transient", retryable=True)
                    if response.status_code != 200:
                        raise HttpActionError("http_rejected")
                    media_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if (
                        media_type != "application/json"
                        or response.headers.get("content-encoding", "identity").lower()
                        != "identity"
                    ):
                        raise HttpActionError("invalid_response")
                    length = response.headers.get("content-length")
                    if length is not None and (
                        len(length) > 8
                        or not length.isdecimal()
                        or int(length) > MAX_RESPONSE_BYTES
                    ):
                        raise HttpActionError("response_too_large")
                    body = bytearray()
                    async for chunk in response.aiter_raw():
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise HttpActionError("response_too_large")
                    try:
                        payload = json.loads(body.decode("utf-8"))
                    except (UnicodeDecodeError, ValueError):
                        raise HttpActionError("invalid_response") from None
                    if not isinstance(payload, dict):
                        raise HttpActionError("invalid_response")
                    output: dict[str, Any] = {"http_status": 200}
                    for field in config.response_fields:
                        if field not in payload:
                            raise HttpActionError("invalid_response")
                        value = payload[field]
                        if not isinstance(value, (bool, int, float)) and value is not None:
                            raise HttpActionError("invalid_response")
                        if isinstance(value, float) and not math.isfinite(value):
                            raise HttpActionError("invalid_response")
                        output[field] = value
                    return output
    except TimeoutError:
        raise HttpActionError("http_timeout", retryable=True) from None
    except httpx.TransportError:
        raise HttpActionError("http_transport", retryable=True) from None
