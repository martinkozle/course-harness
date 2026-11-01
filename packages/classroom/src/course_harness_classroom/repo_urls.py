"""Which repository addresses an Administrator may attach.

Only public HTTPS repositories: no credentials, query or fragment, and no host that names this
machine or a private network, so attaching a course cannot make the Classroom probe internal
services. The host is checked again, after DNS resolution, before every fetch. Local paths,
``file://``, ``http://`` and private hosts are accepted only with ``CLASSROOM_ALLOW_LOCAL_REPOS``,
for development and tests.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from pathlib import Path
from urllib.parse import urlsplit

MAX_URL_LENGTH = 2_048
_HOSTNAME = re.compile(
    r"(?=.{1,253}\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z"
)
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")


class InvalidRepoUrl(ValueError):
    """The address is not one the Classroom may fetch from."""


def validate_repo_url(url: str, *, allow_local: bool = False) -> str:
    """Return the repository address to store, or raise ``InvalidRepoUrl`` with a reason."""
    url = url.strip()
    if not url or len(url) > MAX_URL_LENGTH:
        raise InvalidRepoUrl("Enter the HTTPS address of a public Git repository.")
    if url.startswith("-") or any(character.isspace() or ord(character) < 32 for character in url):
        raise InvalidRepoUrl("A repository address cannot contain spaces or start with a hyphen.")
    if allow_local and url.startswith("/"):
        if not Path(url).is_dir():
            raise InvalidRepoUrl("That local repository does not exist.")
        return url
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
        parts.port  # noqa: B018 - raises for an invalid port
    except ValueError as error:
        raise InvalidRepoUrl("Enter the HTTPS address of a public Git repository.") from error
    allowed_schemes = {"https", "http", "file"} if allow_local else {"https"}
    if parts.scheme not in allowed_schemes:
        raise InvalidRepoUrl("Use an https:// address.")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise InvalidRepoUrl("Leave user names, passwords and tokens out of the address.")
    if parts.query or parts.fragment:
        raise InvalidRepoUrl("A repository address cannot have a query or fragment.")
    if parts.scheme == "file":
        return url
    if not hostname or parts.path in {"", "/"}:
        raise InvalidRepoUrl("Use an https:// address with a repository path.")
    if not allow_local:
        if parts.port not in {None, 443}:
            raise InvalidRepoUrl("Use the standard HTTPS port.")
        check_public_host(hostname)
    return url


def check_public_host(hostname: str) -> None:
    """Refuse a host name or IP literal that names this machine or a non-public network."""
    host = hostname.strip("[]").lower().rstrip(".")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not _HOSTNAME.match(host) or "." not in host:
            raise InvalidRepoUrl("Use the public host name of the repository.") from None
        if host == "localhost" or host.endswith(_LOCAL_SUFFIXES):
            raise InvalidRepoUrl("The Classroom fetches only from public hosts.") from None
        return
    if not is_public_address(address):
        raise InvalidRepoUrl("The Classroom fetches only from public hosts.")


_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_IPV4_COMPATIBLE = ipaddress.ip_network("::/96")


def embedded_ipv4(address: ipaddress.IPv6Address) -> list[ipaddress.IPv4Address]:
    """The IPv4 addresses an IPv6 address carries: mapped, NAT64, compatible, 6to4 or Teredo."""
    embedded: list[ipaddress.IPv4Address] = []
    if address.ipv4_mapped is not None:
        embedded.append(address.ipv4_mapped)
    if address in _NAT64 or (address in _IPV4_COMPATIBLE and int(address) > 1):
        embedded.append(ipaddress.IPv4Address(int(address) & 0xFFFFFFFF))
    if address.sixtofour is not None:
        embedded.append(address.sixtofour)
    if address.teredo is not None:
        embedded.extend(address.teredo)
    return embedded


def is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """A globally routable unicast address, also for every IPv4 address an IPv6 one wraps."""
    if isinstance(address, ipaddress.IPv6Address):
        embedded = embedded_ipv4(address)
        if embedded:
            return all(is_public_address(inner) for inner in embedded)
    return address.is_global and not address.is_multicast


def resolve_public_address(url: str) -> tuple[str, int, str]:
    """Resolve the host once, refuse any non-public answer, and return the address to pin.

    The fetch then connects to exactly this address (``http.curloptResolve``), so a second DNS
    answer cannot redirect it to an internal service.
    """
    parts = urlsplit(url)
    hostname = parts.hostname
    if not hostname:
        raise InvalidRepoUrl("The repository address has no host.")
    port = parts.port or 443
    if port != 443:
        raise InvalidRepoUrl("Use the standard HTTPS port.")
    check_public_host(hostname)
    try:
        infos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except OSError as error:
        raise InvalidRepoUrl(f"The host {hostname} could not be resolved.") from error
    addresses = [ipaddress.ip_address(str(info[4][0]).split("%", 1)[0]) for info in infos]
    if not addresses:
        raise InvalidRepoUrl(f"The host {hostname} could not be resolved.")
    if not all(is_public_address(address) for address in addresses):
        raise InvalidRepoUrl("The repository host resolves to a non-public address.")
    return hostname, port, str(addresses[0])


def check_resolves_publicly(url: str) -> None:
    """Before a fetch, refuse a host that currently resolves to a non-public address."""
    resolve_public_address(url)
