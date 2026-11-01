"""Canonical Course Sources: the Source schema and the ``sources.yaml`` index."""

import ipaddress
import os
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import unquote, unquote_plus, urlsplit, urlunsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from course_harness.core.canonical_mutation import (
    CanonicalFile,
    apply_canonical_mutation,
)
from course_harness.core.canonical_yaml import load_canonical_yaml

MAX_LOCATOR_URL_LENGTH = 2000
DOI_PATTERN = r"^10\.\d{4,9}/[^\s?#]{1,300}$"
# New-style IDs (1706.03762) and old-style ones with an optional subject class (math.GT/0309136).
ARXIV_ID_PATTERN = r"^(?:\d{4}\.\d{4,5}|[a-z][a-z-]{0,20}(?:\.[A-Z]{2})?/\d{7})$"
# Query parameter names that may carry credentials, signatures, sessions or personal data.
_SECRET_QUERY_NAME = re.compile(
    r"key|sig|auth|jwt|hash|hmac|otp|ticket|email|token|secret|session|passw|pwd|credential"
    r"|^code$|^state$|^policy$|^expires$|^sid$|^x-amz-|^x-goog-|^utm_",
    re.IGNORECASE,
)
# Hosts that never name a public site.
_PRIVATE_HOST_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".lan",
    ".corp",
    ".home.arpa",
    ".intranet",
    ".test",
    ".example",
    ".invalid",
)
# Personal share links: whoever holds one can read the file, so it is never a public locator.
_SHARE_LINK_HOSTS = (
    "drive.google.com",
    "dropbox.com",
    "1drv.ms",
    "onedrive.live.com",
    "sharepoint.com",
    "mega.nz",
    "wetransfer.com",
)


def _url_shape_problem(value: str) -> str | None:
    """Why ``value`` cannot be stored as a locator URL at all, if it cannot.

    Deliberately lenient: stricter rules for new locators live in :func:`public_locator`, so
    tightening them never makes an existing ``sources.yaml`` or tagged Release unreadable.
    """
    if len(value) > MAX_LOCATOR_URL_LENGTH or any(
        character.isspace() or not character.isprintable() for character in value
    ):
        return "must be a single printable URL of at most 2000 characters"
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "is not a URL"
    if parsed.scheme not in {"http", "https"}:
        return "must use http or https"
    if parsed.username is not None or parsed.password is not None:
        return "must not contain credentials"
    if not parsed.hostname:
        return "must name a host"
    return None


def _is_public_host(hostname: str) -> bool:
    try:
        return ipaddress.ip_address(hostname).is_global
    except ValueError:
        pass
    if "." not in hostname or hostname.endswith(_PRIVATE_HOST_SUFFIXES):
        return False
    # Dotted numbers that are not a valid address, such as 127.1 or 2130706433.0, still reach
    # private addresses through lenient resolvers.
    return not all(label.isdigit() or label.startswith("0x") for label in hostname.split("."))


def _is_share_link(hostname: str, path: str) -> bool:
    if any(hostname == host or hostname.endswith(f".{host}") for host in _SHARE_LINK_HOSTS):
        return True
    if hostname == "docs.google.com" and "/d/" in path:
        return True
    return hostname in {"box.com", "app.box.com", "www.box.com"} and path.startswith("/s/")


class SourceLocator(BaseModel):
    """Public metadata that lets a reader find a Source: never content, paths or secrets."""

    model_config = ConfigDict(extra="forbid")

    url: str | None = Field(default=None, exclude_if=lambda value: value is None)
    doi: str | None = Field(
        default=None, pattern=DOI_PATTERN, exclude_if=lambda value: value is None
    )
    arxiv_id: str | None = Field(
        default=None, pattern=ARXIV_ID_PATTERN, exclude_if=lambda value: value is None
    )

    @field_validator("url")
    @classmethod
    def url_has_a_safe_shape(cls, value: str | None) -> str | None:
        if value is not None and (problem := _url_shape_problem(value)) is not None:
            raise ValueError(f"Source Locator URL {problem}")
        return value

    @model_validator(mode="after")
    def names_something(self) -> SourceLocator:
        if self.url is None and self.doi is None and self.arxiv_id is None:
            raise ValueError("A Source Locator needs a URL, DOI or arXiv ID")
        return self

    def link(self) -> str:
        """The address a reader opens: the URL, else the DOI or arXiv resolver."""
        if self.url is not None:
            return self.url
        if self.doi is not None:
            return f"https://doi.org/{self.doi}"
        return f"https://arxiv.org/abs/{self.arxiv_id}"


def public_locator(location: str) -> SourceLocator | None:
    """The Source Locator to record for a remote Resource address, or ``None`` when it is not
    a public one: a private or single-label host, a numeric host, or a personal share link.

    Credentials, the fragment, and query parameters that may hold secrets, personal data or
    tracking are dropped; a DOI or arXiv ID is recognised from doi.org and arxiv.org addresses.
    """
    try:
        parsed = urlsplit(location.strip())
        hostname = parsed.hostname
        if parsed.scheme.lower() not in {"http", "https"} or not hostname:
            return None
        port = parsed.port
    except ValueError:
        return None
    hostname = hostname.rstrip(".")
    if not _is_public_host(hostname) or _is_share_link(hostname, parsed.path):
        return None
    host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = host if port is None else f"{host}:{port}"
    query = "&".join(
        part
        for part in parsed.query.split("&")
        if part and not _SECRET_QUERY_NAME.search(unquote_plus(part.split("=", 1)[0]))
    )
    url = urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", query, ""))
    if _url_shape_problem(url) is not None:
        return None
    return SourceLocator(
        url=url, doi=_doi_from(hostname, parsed.path), arxiv_id=_arxiv_from(hostname, parsed.path)
    )


def _doi_from(hostname: str, path: str) -> str | None:
    if hostname not in {"doi.org", "dx.doi.org", "www.doi.org"}:
        return None
    doi = unquote(path).lstrip("/")
    return doi if re.fullmatch(DOI_PATTERN, doi) else None


def _arxiv_from(hostname: str, path: str) -> str | None:
    if hostname not in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}:
        return None
    match = re.fullmatch(r"/(?:abs|pdf|html)/(.+?)(?:v\d+)?(?:\.pdf)?/?", path)
    if match is None:
        return None
    identifier = match.group(1)
    return identifier if re.fullmatch(ARXIV_ID_PATTERN, identifier) else None


def citation_link(citation_url: str | None, locator: SourceLocator | None) -> str | None:
    """Where a Citation links: its own http(s) URL wins, otherwise its Source's locator."""
    if citation_url and _is_http_url(citation_url):
        return citation_url
    return locator.link() if locator is not None else None


def _is_http_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^source-[0-9a-f]{12}$")
    resource_id: str = Field(pattern=r"^resource-[0-9a-f]{12}$")
    source_version_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    label: str = Field(min_length=1, max_length=200)
    admitted_at: str
    # The Document Processor whose representation Citations of this Source point into.
    representation: str | None = Field(default=None, exclude_if=lambda value: value is None)
    # Public metadata for finding the Resource; absent for local uploads and older Sources.
    locator: SourceLocator | None = Field(default=None, exclude_if=lambda value: value is None)


class SourcesIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    sources: list[Source] = Field(default_factory=list)


def with_missing_locators(
    index: SourcesIndex, locators: Mapping[str, SourceLocator]
) -> SourcesIndex | None:
    """Fill each Source's missing locator from ``locators`` by Resource ID.

    A locator already present is never replaced. Returns ``None`` when nothing changes.
    """
    changed = False
    sources: list[Source] = []
    for source in index.sources:
        locator = locators.get(source.resource_id)
        if source.locator is None and locator is not None:
            source = source.model_copy(update={"locator": locator})
            changed = True
        sources.append(source)
    return index.model_copy(update={"sources": sources}) if changed else None


class SourceAdmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    resource_id: str = Field(pattern=r"^resource-[0-9a-f]{12}$")
    label: str | None = Field(default=None, min_length=1, max_length=200)


class InvalidSourcesIndex(ValueError):
    """Canonical Sources state exists but does not satisfy the schema."""


@dataclass(frozen=True)
class SourceImage:
    """The pinned Source Version of an image Source, ready to place on a Slide."""

    source_id: str
    source_version_id: str
    media_type: str
    path: Path


SourceImageResolver = Callable[[str], SourceImage | None]


class PinnedSource(Protocol):
    @property
    def id(self) -> str: ...
    @property
    def resource_id(self) -> str: ...
    @property
    def source_version_id(self) -> str: ...


def read_sources_index(workspace: Path) -> SourcesIndex | None:
    sources_path = workspace / "sources.yaml"
    if not sources_path.exists():
        return None
    try:
        payload = load_canonical_yaml(sources_path.read_text(encoding="utf-8"))
        return SourcesIndex.model_validate(payload)
    except (OSError, UnicodeError, yaml.YAMLError, ValueError) as error:
        raise InvalidSourcesIndex(str(error)) from error


def write_sources_index(
    workspace: Path, index: SourcesIndex, *, expected: CanonicalFile | None = None
) -> None:
    if expected is not None:
        apply_canonical_mutation(
            workspace,
            expected={"sources.yaml": expected},
            updates={"sources.yaml": serialize_sources_index(index)},
        )
        return
    sources_path = workspace / "sources.yaml"
    temporary_path = workspace / f".sources-{uuid.uuid4().hex}.yaml.tmp"
    serialized = serialize_sources_index(index).decode("utf-8")
    with temporary_path.open("x", encoding="utf-8") as stream:
        stream.write(serialized)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        temporary_path.replace(sources_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def serialize_sources_index(index: SourcesIndex) -> bytes:
    """Return the exact canonical bytes written for a Sources index."""
    return yaml.safe_dump(
        index.model_dump(mode="json"), allow_unicode=True, sort_keys=False, width=100
    ).encode("utf-8")
