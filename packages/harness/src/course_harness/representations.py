"""Where each Document Processor's Derived Representation of a Snapshot lives (ADR 0015).

The current representation of a Snapshot is ``derived/<hash>/``. When it is replaced
while a Source pins it, it moves to ``derived/<hash>@<processor>/`` and that Source keeps
resolving to it. A hosted processor's output is also kept in durable Library data, so
clearing the cache never sends the document to the vendor again.
"""

import re
import shutil
from pathlib import Path

from pydantic import ValidationError

from course_harness.document_processors import Conversion, ProcessingRecord

RECORD_NAME = "processing.json"
EXTRACTED_NAME = "extracted.md"
_PROCESSOR_ID = re.compile(r"^[a-z0-9][a-z0-9:._-]{0,120}$")


def representation_key(content_hash: str, processor: str | None = None) -> str:
    """The derived directory name of a current (``processor=None``) or kept representation."""
    if processor is None:
        return content_hash
    if not _PROCESSOR_ID.match(processor):
        raise ValueError(f"Invalid Document Processor ID: {processor}")
    return f"{content_hash}@{processor.replace(':', '~')}"


def read_record(directory: Path) -> ProcessingRecord | None:
    try:
        return ProcessingRecord.model_validate_json((directory / RECORD_NAME).read_bytes())
    except OSError, ValidationError:
        return None


def current_processor(cache_dir: Path, content_hash: str) -> str | None:
    record = read_record(cache_dir / "derived" / content_hash)
    return record.processor if record is not None else None


def evidence_key(cache_dir: Path, content_hash: str, representation: str | None) -> str:
    """The derived directory that holds the representation a Source pinned.

    A Source admitted before representations were recorded, or one that pinned the
    current representation, reads the current one.
    """
    if representation is None:
        return content_hash
    current = current_processor(cache_dir, content_hash)
    if current is None or current == representation:
        return content_hash
    return representation_key(content_hash, representation)


def write_representation(directory: Path, conversion: Conversion, record: ProcessingRecord) -> None:
    """Replace whatever representation the directory held with this one."""
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    (directory / EXTRACTED_NAME).write_text(conversion.markdown, encoding="utf-8")
    if conversion.structured is not None:
        (directory / conversion.structured_name).write_text(conversion.structured, encoding="utf-8")
    (directory / RECORD_NAME).write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")


def keep_current(cache_dir: Path, content_hash: str) -> str | None:
    """Move the current representation aside so a pinning Source keeps resolving to it.

    Returns the key it now lives under, or ``None`` when there was nothing to keep.
    """
    current = cache_dir / "derived" / content_hash
    record = read_record(current)
    if record is None:
        return None
    key = representation_key(content_hash, record.processor)
    destination = cache_dir / "derived" / key
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(current, destination)
    return key


def durable_dir(data_dir: Path, content_hash: str, processor: str) -> Path:
    return data_dir / "representations" / representation_key(content_hash, processor)


def save_durable(data_dir: Path, content_hash: str, source: Path) -> None:
    record = read_record(source)
    if record is None:
        return
    destination = durable_dir(data_dir, content_hash, record.processor)
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)


def restore_durable(data_dir: Path, content_hash: str, processor: str, destination: Path) -> bool:
    """Restore a hosted representation saved earlier, instead of paying for it again."""
    source = durable_dir(data_dir, content_hash, processor)
    if read_record(source) is None or not (source / EXTRACTED_NAME).is_file():
        return False
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)
    return True


def _pin_path(data_dir: Path, content_hash: str, processor: str) -> Path:
    return data_dir / "pins" / representation_key(content_hash, processor)


def pin(data_dir: Path, content_hash: str, processor: str) -> None:
    """Record that a Source pinned this representation, in any Course Workspace."""
    path = _pin_path(data_dir, content_hash, processor)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def is_pinned(data_dir: Path, content_hash: str, processor: str) -> bool:
    return _pin_path(data_dir, content_hash, processor).is_file()


def pinned_processors(data_dir: Path, content_hash: str) -> list[str]:
    pins = data_dir / "pins"
    if not pins.is_dir():
        return []
    prefix = f"{content_hash}@"
    return sorted(
        path.name.removeprefix(prefix).replace("~", ":")
        for path in pins.iterdir()
        if path.name.startswith(prefix)
    )
