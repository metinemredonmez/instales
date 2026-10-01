"""HTTPS delivery to a validated public IP, with TLS verification for the original hostname.

Resolve once per attempt and pin the connection to that address. No redirects or environment
proxies: validation followed by a second DNS lookup would leave a DNS-rebinding SSRF hole.
"""

import http.client
import ipaddress
import re
import socket
import ssl
from urllib.parse import urlsplit


class TargetError(ValueError):
    pass


def _public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return ip.is_global and not ip.is_multicast and not ip.is_reserved


def validate_target(url: str) -> str:
    if len(url) > 2048 or any(ord(c) < 33 or ord(c) > 126 for c in url) or "\\" in url:
        raise TargetError("use an ASCII HTTPS URL without whitespace")
    try:
        parsed = urlsplit(url)
        host, port = parsed.hostname, parsed.port
    except ValueError as exc:
        raise TargetError("invalid target URL") from exc
    if parsed.scheme != "https" or not host or port not in (None, 443) or parsed.username or parsed.password or parsed.fragment:
        raise TargetError("target must be HTTPS on port 443, without credentials or a fragment")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r"[a-zA-Z0-9.-]+", host) or "." not in host or host.rstrip(".").endswith((".localhost", ".local", ".internal")):
            raise TargetError("target must be a public hostname") from None
    else:
        if not _public(str(address)):
            raise TargetError("target must be a public IP address")
    return url


def public_addresses(host: str) -> list[str]:
    addresses = sorted({result[4][0] for result in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
    if not addresses or any(not _public(address) for address in addresses):
        raise TargetError("target resolved to a non-public address")
    return addresses


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str):
        super().__init__(host, timeout=10, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def send(url: str, body: bytes, headers: dict[str, str]) -> int:
    parsed = urlsplit(validate_target(url))
    addresses = public_addresses(parsed.hostname)
    connection = _PinnedHTTPS(parsed.hostname, addresses[0])
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    try:
        connection.request("POST", path, body=body, headers={
            "Content-Type": "application/json", "User-Agent": "InstiLens-Webhooks/1.0", **headers,
        })
        # No remote response body is needed or stored; redirects are terminal failures.
        response = connection.getresponse()
        return response.status
    finally:
        connection.close()
