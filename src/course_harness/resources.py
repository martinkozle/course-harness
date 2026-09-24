import asyncio
import hashlib
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import SplitResult, urljoin, urlsplit
from uuid import uuid4

import httpx2
from pydantic import BaseModel, ConfigDict, Field, model_validator

from course_harness.docling_parser import (
    HTML_MEDIA_TYPES,
    OFFICE_MEDIA_TYPES,
    PDF_MEDIA_TYPE,
    convert_document,
)

ResourceKind = Literal["local-file", "upload", "remote"]
ProcessingStatus = Literal["unprocessed", "processing", "ready", "failed", "retrying"]
MAX_REMOTE_REDIRECTS = 5
RemoteHostResolver = Callable[[str, int], Awaitable[set[str]]]


@dataclass(frozen=True)
class PinnedRemoteRequest:
    url: httpx2.URL
    host_header: str
    sni_hostname: str


class ResourceRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: ResourceKind
    location: str = Field(min_length=1, max_length=2000)
    media_type: str = Field(min_length=1, max_length=100)


class Resource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^resource-[0-9a-f]{12}$")
    kind: ResourceKind
    location: str
    media_type: str
    registered_at: str
    snapshot_hash: str | None = None
    snapshot_history: list[str] = Field(default_factory=list)


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_id: str
    content_hash: str = Field(min_length=64, max_length=64)
    byte_count: int = Field(ge=0)
    captured_at: str


class ResourceState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_id: str
    kind: ResourceKind | None = None
    location: str | None = None
    media_type: str | None = None
    status: ProcessingStatus
    indexed: bool = False
    error: str | None = None
    snapshot: Snapshot | None = None


class LibraryIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    resources: list[Resource] = Field(default_factory=list)


class Candidate(BaseModel):
    """Transient discovery result — never persisted."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    provider_id: str
    title: str | None = None
    authors: list[str] | None = None
    summary: str | None = None
    url: str
    media_type: str | None = None
    size_bytes: int | None = None
    published_at: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    venue: str | None = None
    citation_count: int | None = None
    open_access_url: str | None = None


class DiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=500)
    providers: list[str] | None = None
    limit: int = Field(default=15, ge=1, le=30)


class DiscoveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    candidates: list[Candidate] = Field(default_factory=list)
    error: str | None = None


class RemoteFetchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    url: str = Field(min_length=1, max_length=2000)
    media_type: str | None = None

    @model_validator(mode="after")
    def has_safe_url_shape(self) -> RemoteFetchRequest:
        parse_remote_url(self.url)
        return self


class RemoteSourceRequest(RemoteFetchRequest):
    """A remote URL to capture, process, and admit to the Course in one step."""

    label: str | None = Field(default=None, max_length=200)


def parse_remote_url(value: str) -> SplitResult:
    """Parse an outbound URL and reject schemes or authority forms we never support."""
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Remote URLs must use HTTP or HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Remote URLs cannot contain credentials")
    if not parsed.hostname:
        raise ValueError("Remote URLs must include a hostname")
    try:
        _ = parsed.port
    except ValueError as error:
        raise ValueError("Remote URL has an invalid port") from error
    return parsed


async def resolve_remote_host(hostname: str, port: int) -> set[str]:
    """Resolve an outbound host immediately before connecting to it."""
    try:
        results = await asyncio.to_thread(
            socket.getaddrinfo,
            hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except OSError as error:
        raise ValueError("Remote URL hostname could not be resolved") from error
    addresses = {str(result[4][0]) for result in results}
    if not addresses:
        raise ValueError("Remote URL hostname could not be resolved")
    return addresses


def _is_unsafe_remote_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return True
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    )


async def validate_remote_url(
    value: str,
    *,
    host_resolver: RemoteHostResolver = resolve_remote_host,
) -> set[str]:
    """Reject destinations that could cross the local network trust boundary.

    The resolver is deliberately injectable so callers can test the policy without
    accessing DNS. It is run before every outbound request and the validated addresses
    are returned so a caller can pin the subsequent connection to the same result.
    """
    parsed = parse_remote_url(value)
    assert parsed.hostname is not None
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = await host_resolver(parsed.hostname, port)
    if not addresses:
        raise ValueError("Remote URL hostname could not be resolved")
    if any(_is_unsafe_remote_address(address) for address in addresses):
        raise ValueError("Remote URL resolves to a disallowed network address")
    return addresses


async def pin_remote_url(
    value: str,
    *,
    host_resolver: RemoteHostResolver = resolve_remote_host,
) -> PinnedRemoteRequest:
    """Bind an outbound request to an address approved by the URL policy."""
    addresses = await validate_remote_url(value, host_resolver=host_resolver)
    original = httpx2.URL(value)
    address = min(str(ipaddress.ip_address(candidate.split("%", 1)[0])) for candidate in addresses)
    host = original.raw_host.decode("ascii")
    authority = f"[{host}]" if ":" in host else host
    if original.port is not None:
        authority += f":{original.port}"
    return PinnedRemoteRequest(
        url=original.copy_with(host=address),
        host_header=authority,
        sni_hostname=host,
    )


def redirect_target(current_url: str, location: str | None) -> str:
    if not location:
        raise ValueError("Remote redirect did not include a location")
    return urljoin(current_url, location)


MEDIA_TYPE_PROCESSORS: dict[str, str] = {
    PDF_MEDIA_TYPE: "docling",
    **dict.fromkeys(OFFICE_MEDIA_TYPES, "docling"),
    **dict.fromkeys(HTML_MEDIA_TYPES, "docling"),
    "text/plain": "text",
    "text/markdown": "text",
    "text/x-markdown": "text",
    "text/csv": "text",
    "text/x-python": "code",
    "text/x-python-script": "code",
    "application/x-python-code": "code",
    "application/javascript": "code",
    "text/javascript": "code",
    "application/typescript": "code",
    "application/x-typescript": "code",
    "application/json": "code",
    "text/x-yaml": "code",
    "application/x-yaml": "code",
    "application/toml": "code",
    **dict.fromkeys(("image/png", "image/jpeg", "image/gif"), "image"),
}
IMAGE_MEDIA_TYPES = frozenset(
    media_type for media_type, processor in MEDIA_TYPE_PROCESSORS.items() if processor == "image"
)


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identify_media_type(path_or_name: str, content: bytes | None = None) -> str:
    suffix = Path(path_or_name).suffix.lower()
    mapping: dict[str, str] = {
        ".md": "text/markdown",
        ".txt": "text/plain",
        ".csv": "text/csv",
        ".py": "text/x-python",
        ".js": "application/javascript",
        ".ts": "application/typescript",
        ".tsx": "application/x-typescript",
        ".json": "application/json",
        ".yaml": "text/x-yaml",
        ".yml": "text/x-yaml",
        ".toml": "application/toml",
        ".pdf": "application/pdf",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".ipynb": "application/x-ipynb+json",
        ".html": "text/html",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
    }
    return mapping.get(suffix, "application/octet-stream")


def resolve_processor(media_type: str) -> str | None:
    return MEDIA_TYPE_PROCESSORS.get(media_type)


def register_resource(registry_path: Path, request: ResourceRegistrationRequest) -> Resource:
    index = read_library_index(registry_path)
    resource = Resource(
        id=f"resource-{uuid4().hex[:12]}",
        kind=request.kind,
        location=request.location,
        media_type=request.media_type,
        registered_at=datetime.now(UTC).isoformat(),
    )
    index.resources.append(resource)
    write_library_index(registry_path, index)
    return resource


def update_resource_snapshot(
    registry_path: Path,
    resource_id: str,
    snapshot_hash: str,
    media_type: str | None = None,
) -> Resource | None:
    index = read_library_index(registry_path)
    for idx, resource in enumerate(index.resources):
        if resource.id == resource_id:
            old_hash = resource.snapshot_hash
            history = list(resource.snapshot_history)
            if old_hash is not None and old_hash != snapshot_hash and old_hash not in history:
                history.append(old_hash)
            updated = resource.model_copy(
                update={
                    "snapshot_hash": snapshot_hash,
                    "snapshot_history": history,
                    "media_type": media_type or resource.media_type,
                }
            )
            index.resources[idx] = updated
            write_library_index(registry_path, index)
            return updated
    return None


def create_snapshot(snapshots_dir: Path, resource_id: str, content: bytes) -> Snapshot:
    hash_value = content_hash(content)
    snapshot_path = snapshots_dir / hash_value
    if not snapshot_path.exists():
        snapshots_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_bytes(content)
    return Snapshot(
        resource_id=resource_id,
        content_hash=hash_value,
        byte_count=len(content),
        captured_at=datetime.now(UTC).isoformat(),
    )


def process_snapshot(
    cache_dir: Path, content_hash: str, media_type: str, content: bytes
) -> ResourceState:
    processor_name = resolve_processor(media_type)
    derived_dir = cache_dir / "derived" / content_hash
    derived_dir.mkdir(parents=True, exist_ok=True)

    if processor_name == "text":
        derived_dir.mkdir(parents=True, exist_ok=True)
        (derived_dir / "extracted.md").write_bytes(content)
        return ResourceState(
            resource_id="",
            status="ready",
        )

    if processor_name == "code":
        derived_dir.mkdir(parents=True, exist_ok=True)
        raw = content.decode("utf-8", errors="replace")
        fenced = f"```\n{raw}\n```\n"
        (derived_dir / "extracted.md").write_text(fenced, encoding="utf-8")
        return ResourceState(
            resource_id="",
            status="ready",
        )

    if processor_name == "image":
        try:
            reference = describe_image(media_type, content)
        except ValueError as error:
            return ResourceState(resource_id="", status="failed", error=str(error))
        (derived_dir / "extracted.md").write_text(reference, encoding="utf-8")
        return ResourceState(resource_id="", status="ready")

    if processor_name == "docling":
        try:
            extracted, structured = convert_document(media_type, content, cache_dir)
        except Exception as error:
            return ResourceState(
                resource_id="",
                status="failed",
                error=f"Could not process the document: {error}",
            )
        if not extracted.strip():
            return ResourceState(
                resource_id="",
                status="failed",
                error="No readable content was found in the document.",
            )
        (derived_dir / "extracted.md").write_text(extracted, encoding="utf-8")
        (derived_dir / "docling.json").write_text(structured, encoding="utf-8")
        return ResourceState(resource_id="", status="ready")

    return ResourceState(
        resource_id="",
        status="unprocessed",
        error=f"No processor available for {media_type}; content stored as immutable Snapshot.",
    )


def image_dimensions(content: bytes) -> tuple[int, int]:
    """Return an image's pixel size, rejecting content that is not a readable image."""
    from io import BytesIO  # noqa: PLC0415

    from PIL import Image, UnidentifiedImageError  # noqa: PLC0415

    try:
        with Image.open(BytesIO(content)) as image:
            image.verify()
            return image.size
    except (UnidentifiedImageError, OSError, SyntaxError) as error:
        raise ValueError("The image could not be read.") from error


def describe_image(media_type: str, content: bytes) -> str:
    """Build the textual Derived Representation of an image.

    It is what a model without vision input knows about the image, and what a
    Citation of the image resolves to.
    """
    width, height = image_dimensions(content)
    return f"Image ({media_type}), {width} × {height} px, {len(content)} bytes.\n"


def read_library_index(registry_path: Path) -> LibraryIndex:
    if not registry_path.is_file():
        return LibraryIndex()
    try:
        return LibraryIndex.model_validate_json(registry_path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return LibraryIndex()


def write_library_index(registry_path: Path, index: LibraryIndex) -> None:
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(index.model_dump_json(indent=2) + "\n", encoding="utf-8")
