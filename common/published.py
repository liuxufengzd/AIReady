"""Published files on the shared document store.

One accepted file lives at ``store/s3/processed/{project}/{filename}/``.
Chunk text is ``chunks.json`` in that directory. File-level fields such as
``extension`` are stored in PostgreSQL, not in this file.
"""

import json
from dataclasses import dataclass
from pathlib import Path

CHUNKS_NAME = "chunks.json"
# published.py -> common -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class PublishedChunk:
    id: str
    semantic_text: str
    keyword_text: str
    retrieve_raw_file: bool


def store_root() -> Path:
    """Default document store: ``{repo}/store/s3``."""
    return _REPO_ROOT / "store" / "s3"


def published_dir(project: str, filename: str) -> Path:
    """Directory that holds one published file and its chunks."""
    base = store_root().resolve()
    target = (
        base
        / "processed"
        / _component(project, "project")
        / _component(filename, "filename")
    ).resolve()
    if not target.is_relative_to(base):
        raise ValueError(f"Path escapes the document store: {target}")
    return target


def chunks_path(project: str, filename: str) -> Path:
    return published_dir(project, filename) / CHUNKS_NAME


def load_chunks(project: str, filename: str) -> list[PublishedChunk]:
    """Read the chunk list published for one file."""
    path = chunks_path(project, filename)
    if not path.is_file():
        raise FileNotFoundError(f"Published chunks not found: {path}")
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Published chunks are not valid JSON: {path}") from exc
    if not isinstance(records, list) or not records:
        raise ValueError(f"Published chunks must be a non-empty list: {path}")

    return [_chunk(record, path, index) for index, record in enumerate(records)]


def published_parse_source(project: str, filename: str) -> Path:
    """File returned when a chunk asks for the original document.

    PDF and image publishes keep the original name. An Office file is stored
    as the PDF that was parsed, ``{stem}.pdf``, in the same directory.
    """
    directory = published_dir(project, filename)
    named = directory / filename
    if named.is_file():
        return named
    converted = directory / f"{Path(filename).stem}.pdf"
    if converted.is_file():
        return converted
    return named


def _chunk(record: object, path: Path, index: int) -> PublishedChunk:
    if not isinstance(record, dict):
        raise ValueError(f"Chunk {index} in {path} is not an object")
    chunk_id = record.get("id")
    semantic_text = record.get("semantic_text")
    keyword_text = record.get("keyword_text")
    retrieve_raw_file = record.get("retrieve_raw_file", False)
    if not isinstance(chunk_id, str) or not chunk_id.strip():
        raise ValueError(f"Chunk {index} in {path} is missing an id")
    if not isinstance(semantic_text, str) or not semantic_text.strip():
        raise ValueError(f"Chunk {chunk_id} in {path} is missing semantic text")
    if not isinstance(keyword_text, str) or not keyword_text.strip():
        raise ValueError(f"Chunk {chunk_id} in {path} is missing keyword text")
    if not isinstance(retrieve_raw_file, bool):
        raise ValueError(f"Chunk {chunk_id} in {path} has an invalid retrieve_raw_file")
    return PublishedChunk(
        id=chunk_id,
        semantic_text=semantic_text,
        keyword_text=keyword_text,
        retrieve_raw_file=retrieve_raw_file,
    )


def _component(value: str, label: str) -> str:
    """Reject names that would leave ``processed``."""
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or value != Path(value).name
    ):
        raise ValueError(f"Invalid {label}: {value!r}")
    return value
