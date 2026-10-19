"""Which URLs the platform will call for a webhook, and how it reaches them.

An endpoint URL is typed in by a tenant and called from inside the platform's own
network, so it is checked when the endpoint is saved and again before every delivery
(program D8, spec §9.2).

* **Outside production** only the shape of the URL is checked: ``http`` or ``https``, a
  host, no credentials, a port in range, at most ``MAX_URL_LENGTH`` characters. The URL
  is called as written, so a receiver on ``localhost`` works.
* **In production** the URL has to be ``https`` and every address its host resolves to
  has to be public: not loopback, link-local, private (RFC 1918 and the other special
  ranges), multicast, reserved or unspecified, not a cloud metadata address, and not an
  IPv6 spelling that carries one of those (an IPv4-mapped address, the NAT64 prefix).
  An answer that mixes public and other addresses is refused whole.
  The check is made on the answer of one lookup, and the request is then sent to the
  address that was checked, with the name in the ``Host`` header and in the TLS server
  name (so the certificate is still checked against the name): a second lookup by the
  HTTP client could answer differently (DNS rebinding).

A refusal names the rule and never the URL, the host or the address: URLs often carry a
token in the path, and the message ends up in a response, a log line and a column.
"""

import ipaddress
import re
import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network
from typing import ClassVar, Self

import httpx

from xolotl.platform.config import Settings
from xolotl.platform.errors import ValidationFailedError

Resolver = Callable[[str], Sequence[str]]

MAX_URL_LENGTH = 2048
_MAX_HOST_LENGTH = 253
_MAX_PORT = 65535

# Lower-case labels of letters, digits, hyphens and underscores, as the ASCII form of
# an international name has them. A percent sign, a space or anything else is not a
# name the HTTP client and the operating system would read the same way.
_HOST = re.compile(r"^(?:[a-z0-9_-]{1,63}\.)*[a-z0-9_-]{1,63}\.?$")

# Addresses that are public by every list but only answer inside one cloud: Azure's
# wire server. The other metadata addresses (169.254.169.254 and its IPv6 and ECS
# cousins, Alibaba's 100.100.100.200) already fall in ranges that are not global.
_BLOCKED = (IPv4Network("168.63.129.16/32"),)
# NAT64 carries an IPv4 address in its last 32 bits; the address behind it decides.
_NAT64 = IPv6Network("64:ff9b::/96")

_NOT_VALID = "webhook url is not valid"
_TOO_LONG = "webhook url is too long"
_CREDENTIALS = "webhook url must not carry credentials"
_HTTPS_ONLY = "webhook url must use https"
_HTTP_OR_HTTPS = "webhook url must use http or https"
_NO_ADDRESS = "webhook host does not resolve to an address"
_NON_PUBLIC = "webhook url points at a non-public address"


class WebhookUrlError(ValidationFailedError):
    """A webhook URL the platform will not call; the message names the rule only."""

    default_detail: ClassVar[str] = "webhook url is not allowed"


def system_resolver(host: str) -> list[str]:
    """Ask the operating system for the addresses of ``host``.

    This is the lookup the HTTP client would make, so a spelling it reads as an
    address (``2130706433``, ``0x7f.0.0.1``, ``127.1``) comes back as that address. A
    name that does not resolve raises ``OSError`` (``socket.gaierror``).

    Returns:
        The distinct addresses, in the order the system gave them.

    """
    answers = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(answer[4][0]) for answer in answers))


def _is_public(address: IPv4Address | IPv6Address) -> bool:
    if isinstance(address, IPv6Address):
        if address.ipv4_mapped is not None:
            return _is_public(address.ipv4_mapped)
        if address in _NAT64:
            return _is_public(IPv4Address(address.packed[-4:]))
    return (
        address.is_global
        # Python counts multicast and reserved space as global.
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified
        and not address.is_loopback
        and not address.is_link_local
        and not any(address in network for network in _BLOCKED)
    )


def is_public_address(address: str) -> bool:
    """Say whether the platform may call ``address``.

    Returns:
        ``True`` for a public address; ``False`` for anything else, text that is not an
        address included.

    """
    try:
        return _is_public(ipaddress.ip_address(address))
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class Destination:
    """Where one delivery request goes and how to address it.

    ``url`` is what to request. For a name checked in production its host is the
    address that was checked, ``host`` is the ``Host`` header to send with it and
    ``sni_hostname`` the name the TLS certificate has to match. A URL called as written
    has neither.
    """

    url: str
    host: str | None = None
    sni_hostname: str | None = None


@dataclass(frozen=True, slots=True)
class UrlPolicy:
    """The rules for webhook URLs: strict in production, shape only elsewhere."""

    strict: bool
    resolver: Resolver = system_resolver

    @classmethod
    def for_settings(
        cls, settings: Settings, *, resolver: Resolver = system_resolver
    ) -> Self:
        """Build the policy ``settings`` asks for: strict when ``env`` is ``prod``.

        Returns:
            The policy.

        """
        return cls(strict=settings.env == "prod", resolver=resolver)

    def destination(self, url: str) -> Destination:
        """Check ``url`` and say where to send a request for it.

        In production this resolves the host, so call it again before every request
        that is made.

        Returns:
            The destination to request.

        Raises:
            WebhookUrlError: When the URL breaks a rule; the message says which.

        """
        text = url.strip()
        parsed = _parse(text)
        allowed = {"https"} if self.strict else {"http", "https"}
        if parsed.scheme not in allowed:
            raise WebhookUrlError(_HTTPS_ONLY if self.strict else _HTTP_OR_HTTPS)
        if parsed.userinfo:
            raise WebhookUrlError(_CREDENTIALS)
        host = _host(parsed)
        if not self.strict:
            return Destination(url=text)
        if _is_address(host):
            if not is_public_address(host):
                raise WebhookUrlError(_NON_PUBLIC)
            return Destination(url=text)
        address = self._resolve(host)[0]
        authority = host if parsed.port is None else f"{host}:{parsed.port}"
        return Destination(
            url=str(parsed.copy_with(host=str(address))),
            host=authority,
            sni_hostname=host,
        )

    def _resolve(self, host: str) -> list[IPv4Address | IPv6Address]:
        """Look ``host`` up and check every address in the answer.

        Returns:
            The addresses, IPv4 before IPv6: a host with no IPv6 route fails to connect
            to the first address it is given.

        Raises:
            WebhookUrlError: When the name does not resolve or any address is not
                public.

        """
        try:
            answers = list(self.resolver(host))
        except OSError:
            raise WebhookUrlError(_NO_ADDRESS) from None
        try:
            addresses = [ipaddress.ip_address(answer) for answer in answers]
        except ValueError:
            raise WebhookUrlError(_NO_ADDRESS) from None
        if not addresses:
            raise WebhookUrlError(_NO_ADDRESS)
        if not all(_is_public(address) for address in addresses):
            raise WebhookUrlError(_NON_PUBLIC)
        return sorted(addresses, key=lambda address: address.version)


def _parse(text: str) -> httpx.URL:
    if len(text) > MAX_URL_LENGTH:
        raise WebhookUrlError(_TOO_LONG)
    try:
        return httpx.URL(text)
    except httpx.InvalidURL:
        # The client's message quotes the part of the URL it choked on.
        raise WebhookUrlError(_NOT_VALID) from None


def _is_address(host: str) -> bool:
    try:
        _ = ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _host(parsed: httpx.URL) -> str:
    """Validate the host and port of a parsed URL.

    Returns:
        The host as the HTTP client sends it: lower case, an international name as
        punycode, an IPv6 address without brackets.

    Raises:
        WebhookUrlError: When the host is missing or malformed or the port is out of
            range.

    """
    host = parsed.raw_host.decode("ascii")
    if parsed.port is not None and not 1 <= parsed.port <= _MAX_PORT:
        raise WebhookUrlError(_NOT_VALID)
    # The only host with a colon is an IPv6 literal, which the client has already
    # checked; anything else has to look like a name.
    if ":" not in host and (len(host) > _MAX_HOST_LENGTH or not _HOST.match(host)):
        raise WebhookUrlError(_NOT_VALID)
    return host
