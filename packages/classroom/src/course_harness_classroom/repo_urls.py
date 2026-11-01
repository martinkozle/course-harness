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


def is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast


def check_resolves_publicly(url: str) -> None:
    """Before a fetch, refuse a host that currently resolves to a non-public address."""
    hostname = urlsplit(url).hostname
    if not hostname:
        raise InvalidRepoUrl("The repository address has no host.")
    check_public_host(hostname)
    try:
        infos = socket.getaddrinfo(hostname, 443, proto=socket.IPPROTO_TCP)
    except OSError as error:
        raise InvalidRepoUrl(f"The host {hostname} could not be resolved.") from error
    for info in infos:
        address = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
        if not is_public_address(address):
            raise InvalidRepoUrl("The repository host resolves to a non-public address.")
