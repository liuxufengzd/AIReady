"""Raw files, their pipeline state, and the documents published from them.

State of one raw file ``raw/{project}/{filename}``:

- ``extracting``  an extraction request is waiting for, or running in, dataprep
- ``failed``      the last extraction run failed or was canceled
- ``reviewing``   extraction finished and the review decision is empty
- ``indexing``    reviewed, but publish or search indexing has not finished
- ``ready``       published and indexed
- ``None``        nothing has run yet, or the document was deleted back to raw

Only a file with no state, or a failed one, can be queued for extraction.
Only a ready document can be deleted.
"""

import asyncio
import json
import mimetypes
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from common.const import MARKDOWN_NAME, SUPPORTED_FILE_TYPES
from common.logger import get_logger
from common.published import chunks_path, load_chunks, published_parse_source
from data_management.dao.extraction_request import (
    delete_extraction_request,
    list_project_requests,
    queue_extraction,
)
from data_management.dao.file_metadata import (
    delete_file_metadata,
    list_project_file_metadata,
)
from data_management.dao.review import (
    delete_file_reviews,
    list_review_projects,
    list_reviews,
)
from data_management.service import Conflict, TokenCountError, count_tokens
from data_management.store import (
    LAYOUT_NAME,
    RAW_DIRNAME,
    asset_under,
    component,
    processed_dir,
    raw_file,
    raw_root,
    under,
)

logger = get_logger(__name__)

_SUPPORTED_SUFFIXES = frozenset(SUPPORTED_FILE_TYPES)
_SEARCH_LIMIT = 1000
_TOKEN_WORKERS = 8
_EXTRACTABLE_STATES = {None, "failed"}


class SearchIndexError(Exception):
    """The search service could not remove the document."""


class LibraryService:
    """Browse raw files, queue extraction, and manage published documents."""

    def __init__(self) -> None:
        # (chunks.json path, mtime_ns) -> token count per chunk text
        self._token_cache: dict[tuple[str, int], dict[str, int]] = {}
        self._token_lock = Lock()

    # ------------------------------------------------------------------
    # Raw files
    # ------------------------------------------------------------------

    def projects(self) -> list[str]:
        """Projects with raw files, or with documents whose raw file is gone."""
        names = set(list_review_projects())
        root = raw_root()
        if root.is_dir():
            names |= {
                child.name
                for child in root.iterdir()
                if child.is_dir() and not child.name.startswith(".")
            }
        return sorted(names)

    def browse(self, path: str = "", query: str = "") -> dict[str, Any]:
        """List one directory under ``raw``, or search below it by file name."""
        root = raw_root()
        directory = _raw_path(path)
        if not directory.is_dir():
            raise FileNotFoundError(f"Directory not found: raw/{path}")

        needle = query.strip().lower()
        truncated = False
        if needle:
            children = []
            for child in sorted(directory.rglob("*")):
                if _hidden(child, root) or needle not in child.name.lower():
                    continue
                if len(children) == _SEARCH_LIMIT:
                    truncated = True
                    break
                children.append(child)
        else:
            children = [child for child in directory.iterdir() if not _hidden(child, root)]

        entries = [_entry(child, root) for child in children]
        self._attach_states(entries)
        entries.sort(key=lambda e: (e["kind"] != "dir", e["path"].lower()))
        return {
            "path": _relative(directory, root),
            "entries": entries,
            "truncated": truncated,
        }

    def raw_file_path(self, path: str) -> Path:
        target = _raw_path(path)
        if not target.is_file():
            raise FileNotFoundError(f"File not found: raw/{path}")
        return target

    def queue_extractions(self, files: list[tuple[str, str]]) -> list[dict[str, Any]]:
        """Queue raw files for dataprep. Each result says whether it was queued."""
        states: dict[str, dict[str, tuple[str | None, str | None]]] = {}
        results = []
        for project, filename in files:
            reason = None
            try:
                source = raw_file(project, filename)
                if project not in states:
                    states[project] = self._project_states(project)
                state, _ = states[project].get(filename, (None, None))
                if not source.is_file():
                    reason = "The raw file does not exist."
                elif source.suffix.lower() not in _SUPPORTED_SUFFIXES:
                    reason = f"{source.suffix or 'This'} files cannot be extracted."
                elif source.stat().st_size <= 0:
                    reason = "The raw file is empty."
                elif state not in _EXTRACTABLE_STATES:
                    reason = f"The file is already {state}."
                elif not queue_extraction(project, filename):
                    reason = "The file is already queued."
            except ValueError as exc:
                reason = str(exc)
            if reason is None:
                logger.info("Queued extraction for %s/%s", project, filename)
            results.append(
                {
                    "project": project,
                    "filename": filename,
                    "queued": reason is None,
                    "reason": reason,
                }
            )
        return results

    # ------------------------------------------------------------------
    # Ready documents
    # ------------------------------------------------------------------

    def ready_documents(self, project: str) -> list[dict[str, Any]]:
        """Published, indexed documents in one project."""
        project = component(project, "project")
        metadata = {row["filename"]: row for row in list_project_file_metadata(project)}
        documents = []
        for row in _latest_reviews(project).values():
            if _review_state(row) != "ready":
                continue
            meta = metadata.get(row["filename"], {})
            documents.append(
                {
                    **_document_row(row),
                    "mime_type": meta.get("mime_type")
                    or mimetypes.guess_type(row["filename"])[0],
                    "size": meta.get("size"),
                    "chunk_count": _chunk_count(project, row["filename"]),
                }
            )
        documents.sort(key=lambda d: d["filename"].lower())
        return documents

    def document(self, project: str, filename: str) -> dict[str, Any]:
        """A published document: its review row and the markdown it was indexed from."""
        row = self._published_row(project, filename)
        directory = processed_dir(project, filename)
        markdown = directory / MARKDOWN_NAME
        source = published_parse_source(project, filename)
        return {
            **_document_row(row),
            "content": markdown.read_text(encoding="utf-8") if markdown.is_file() else None,
            "has_layout": (directory / LAYOUT_NAME).is_file(),
            "source_name": source.name if source.is_file() else None,
        }

    def document_file(self, project: str, filename: str, relative: str) -> Path:
        """A file inside a published document's directory, such as an image."""
        self._published_row(project, filename)
        return asset_under(processed_dir(project, filename), relative)

    def document_source(self, project: str, filename: str) -> Path:
        """The PDF or image that was parsed or described."""
        self._published_row(project, filename)
        source = published_parse_source(project, filename)
        if not source.is_file():
            raise FileNotFoundError(f"Parse source not found for {project}/{filename}")
        return source

    def chunks(self, project: str, filename: str) -> list[dict[str, Any]]:
        """Published chunks with the token count of each text."""
        self._published_row(project, filename)
        chunks = load_chunks(project, filename)
        counts = self._token_counts(
            chunks_path(project, filename),
            [text for chunk in chunks for text in (chunk.semantic_text, chunk.keyword_text)],
        )
        return [
            {
                "id": chunk.id,
                "semantic_text": chunk.semantic_text,
                "keyword_text": chunk.keyword_text,
                "retrieve_raw_file": chunk.retrieve_raw_file,
                "semantic_tokens": counts[chunk.semantic_text],
                "keyword_tokens": counts[chunk.keyword_text],
            }
            for chunk in chunks
        ]

    async def delete_document(self, project: str, filename: str) -> None:
        """Remove a ready document from search and processed, back to its raw state."""
        project = component(project, "project")
        filename = component(filename, "filename")
        states = await asyncio.to_thread(self._project_states, project)
        state, _ = states.get(filename, (None, None))
        if state != "ready":
            raise Conflict(
                f"{project}/{filename} is {state or 'not processed'}; only ready documents can be deleted."
            )

        # Index first: if search is down, nothing else has changed.
        try:
            from grpc_protos.search.search_client import SearchClient

            async with SearchClient(project) as client:
                await client.delete(filename)
        except Exception as exc:
            logger.exception("Search delete failed for %s/%s", project, filename)
            raise SearchIndexError(
                f"Could not remove {filename} from the search index: {exc}"
            ) from exc

        await asyncio.to_thread(self._forget, project, filename)
        logger.info("Deleted %s/%s back to its raw state", project, filename)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _forget(self, project: str, filename: str) -> None:
        # Rows first: a leftover directory is overwritten by the next extraction,
        # while rows without files would show a ready document that is gone.
        delete_file_reviews(project, filename)
        delete_extraction_request(project, filename)
        delete_file_metadata(project, filename)
        directory = processed_dir(project, filename)
        if directory.exists():
            shutil.rmtree(directory)

    def _published_row(self, project: str, filename: str) -> dict[str, Any]:
        project = component(project, "project")
        filename = component(filename, "filename")
        row = _latest_reviews(project).get(filename)
        if row is None or not row["processed"]:
            raise FileNotFoundError(f"{project}/{filename} has not been published.")
        return row

    def _project_states(self, project: str) -> dict[str, tuple[str | None, str | None]]:
        """``filename -> (state, error)`` for every file with a pipeline record."""
        reviews = _latest_reviews(project)
        states: dict[str, tuple[str | None, str | None]] = {
            filename: (_review_state(row), None) for filename, row in reviews.items()
        }
        for request in list_project_requests(project):
            review = reviews.get(request["filename"])
            # A request newer than the review is a fresh extraction of the file.
            if review is None or request["request_time"] > review["create_time"]:
                if request["status"] == "failed":
                    states[request["filename"]] = ("failed", request["error"])
                else:
                    states[request["filename"]] = ("extracting", None)
        return states

    def _attach_states(self, entries: list[dict[str, Any]]) -> None:
        by_project: dict[str, dict[str, tuple[str | None, str | None]]] = {}
        for entry in entries:
            project = entry.get("project")
            if entry["kind"] != "file" or project is None:
                continue
            if project not in by_project:
                by_project[project] = self._project_states(project)
            state, error = by_project[project].get(entry["name"], (None, None))
            entry["state"] = state
            entry["error"] = error
            entry["extractable"] = entry["supported"] and state in _EXTRACTABLE_STATES

    def _token_counts(self, path: Path, texts: list[str]) -> dict[str, int]:
        key = (str(path), path.stat().st_mtime_ns)
        with self._token_lock:
            cached = self._token_cache.get(key)
        if cached is not None:
            return cached

        unique = list(dict.fromkeys(texts))
        try:
            with ThreadPoolExecutor(max_workers=_TOKEN_WORKERS) as pool:
                counts = dict(zip(unique, pool.map(count_tokens, unique)))
        except Exception as exc:
            logger.exception("Token count failed for %s", path)
            raise TokenCountError("Could not count tokens for the chunks.") from exc

        with self._token_lock:
            self._token_cache = {
                k: v for k, v in self._token_cache.items() if k[0] != key[0]
            }
            self._token_cache[key] = counts
        return counts


def _latest_reviews(project: str) -> dict[str, dict[str, Any]]:
    """The newest review row of each file, across engines."""
    latest: dict[str, dict[str, Any]] = {}
    for row in list_reviews(project):
        current = latest.get(row["filename"])
        if current is None or row["create_time"] > current["create_time"]:
            latest[row["filename"]] = row
    return latest


def _review_state(row: dict[str, Any]) -> str:
    if row["approved"] is None:
        return "reviewing"
    if not row["processed"] or not row["indexed"]:
        return "indexing"
    return "ready"


def _document_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "project": row["project"],
        "filename": row["filename"],
        "engine": row["engine"],
        "token_num": int(row["token_num"]),
        "approved": bool(row["approved"]),
        "require_chunking": bool(row["require_chunking"]),
        "create_time": row["create_time"],
    }


def _chunk_count(project: str, filename: str) -> int | None:
    try:
        records = json.loads(chunks_path(project, filename).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return len(records) if isinstance(records, list) else None


def _raw_path(path: str) -> Path:
    """A path under ``raw`` from a ``/``-separated relative path."""
    parts = [part for part in (path or "").split("/") if part]
    if ".." in parts:
        raise ValueError(f"Invalid path: {path!r}")
    target = under(RAW_DIRNAME, *parts)
    # A symlink can still point outside raw.
    if not target.is_relative_to(raw_root()):
        raise ValueError(f"Invalid path: {path!r}")
    return target


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix() if path != root else ""


def _hidden(path: Path, root: Path) -> bool:
    return any(part.startswith(".") for part in path.relative_to(root).parts)


def _entry(path: Path, root: Path) -> dict[str, Any]:
    stat = path.stat()
    relative = _relative(path, root)
    parts = relative.split("/")
    modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    if path.is_dir():
        return {"name": path.name, "path": relative, "kind": "dir", "modified": modified}

    # Only raw/{project}/{filename} is a document the pipeline can process.
    is_document = len(parts) == 2
    supported = (
        is_document
        and path.suffix.lower() in _SUPPORTED_SUFFIXES
        and stat.st_size > 0
    )
    return {
        "name": path.name,
        "path": relative,
        "kind": "file",
        "size": stat.st_size,
        "mime_type": mimetypes.guess_type(path.name)[0],
        "modified": modified,
        "project": parts[0] if is_document else None,
        "supported": supported,
        "state": None,
        "error": None,
        "extractable": False,
    }
