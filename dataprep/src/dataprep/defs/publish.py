"""Publish a reviewed document.

``reviewed_document_sensor`` watches review rows with ``processed = false`` and
``approved`` set, then launches ``publish_document``:

``approved`` -> ``chunk_or_skip`` -> ``publish_files`` -> ``index``
``rejected`` -> ``vlm`` -> ``publish_files`` -> ``index``

Both branch assets are in the job. The one that does not apply to this
decision returns immediately. The branch that runs writes chunk text to
``tmp/chunks.json``. ``publish_files`` moves the accepted files out of
``tmp`` into ``processed/{project}/{filename}/``, stores the small file metadata in
PostgreSQL, and sets ``processed = true``. ``index`` asks search to index
``chunks.json``. A failed index is rerun on its own, without a new publish job.
"""

import json
import mimetypes
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any, TypedDict

import dagster as dg
from langchain_text_splitters import MarkdownTextSplitter

from dataprep.common import const
from dataprep.common.utils import document_key, document_partitions, parse_document_key
from dataprep.dao.file_metadata import save_file_metadata
from dataprep.dao.review import get_review, list_ready_reviews, mark_review_processed
from dataprep.resources.dbclient import DBClient
from dataprep.resources.local_doc_store import LocalDocStore
from dataprep.resources.vlm_extractor import VLMExtractor

_RETRY = dg.RetryPolicy(max_retries=2)
_ENGINE_TAG = "review_engine"
_CHUNKS_NAME = "chunks.json"
# defs/publish.py -> defs -> dataprep -> src -> dataprep project -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[4]


class ChunkText(TypedDict):
    semantic_text: str
    keyword_text: str
    retrieve_raw_file: bool


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _find_parse_source(directory: Path, filename: str) -> Path:
    """Return the original PDF or image to parse."""
    named = directory / filename
    if named.is_file():
        return named
    converted = directory / f"{Path(filename).stem}.pdf"
    if converted.is_file():
        return converted
    raise FileNotFoundError(f"Parse source for {filename} not found in {directory}")


def _content_dir(store: LocalDocStore, project: str, filename: str) -> Path:
    """Directory that currently holds the parse: tmp, or the published folder."""
    working = store.working_dir(project, filename)
    if working.is_dir() and any(working.iterdir()):
        return working
    published = store.processed_dir(project, filename)
    if published.is_dir():
        return published
    raise FileNotFoundError(f"No extraction output for {project}/{filename}")


def _publish_reviewed_files(
    store: LocalDocStore, project: str, filename: str, *, approved: bool
) -> Path:
    """Move accepted files from ``tmp`` up into ``processed/{project}/{filename}/``.

    An approved parse keeps the tmp contents. A rejected parse keeps the PDF
    or image the vision model read, plus ``chunks.json``. Anything else in the
    directory, including ``tmp``, is removed.
    """
    dest = store.processed_dir(project, filename)
    working = store.working_dir(project, filename)
    if not working.is_dir():
        if dest.is_dir():
            return dest
        kind = "approved" if approved else "rejected"
        raise FileNotFoundError(f"No {kind} parse to publish for {project}/{filename}")

    if approved:
        incoming = list(working.iterdir())
    else:
        source = _find_parse_source(working, filename)
        chunks = working / _CHUNKS_NAME
        if not chunks.is_file():
            raise FileNotFoundError(f"No chunks to publish for {project}/{filename}")
        incoming = [source, chunks]

    # A retry can find an empty tmp after the files were already moved up.
    if not incoming:
        _remove(working)
        return dest

    kept = {path.name for path in incoming}
    for path in incoming:
        target = dest / path.name
        if target.exists():
            _remove(target)
        path.rename(target)
    for child in list(dest.iterdir()):
        if child.name not in kept:
            _remove(child)
    return dest


def _chunk_markdown(text: str) -> list[str]:
    splitter = MarkdownTextSplitter(
        chunk_size=const.DEFAULT_CHUNK_SIZE,
        chunk_overlap=const.DEFAULT_CHUNK_OVERLAP,
    )
    return [part for part in splitter.split_text(text) if part.strip()]


def _save_chunks(directory: Path, chunks: list[ChunkText]) -> Path:
    """Store chunks in the parse directory."""
    path = directory / _CHUNKS_NAME
    records = [
        {
            "id": str(uuid.uuid4()),
            "semantic_text": chunk["semantic_text"],
            "keyword_text": chunk["keyword_text"],
            "retrieve_raw_file": chunk["retrieve_raw_file"],
        }
        for chunk in chunks
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(records, ensure_ascii=False, indent=4),
        encoding="utf-8",
    )
    return path


async def _index_chunks(project: str, filename: str) -> None:
    """Ask the search service to index the published chunks for this file."""
    root = str(_REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from grpc_protos.search.search_client import SearchClient

    async with SearchClient(project) as client:
        await client.store(filename)


def _ready_review(context: dg.AssetExecutionContext, db: DBClient) -> dict[str, Any]:
    """Load the review row this run is publishing."""
    project, filename = parse_document_key(context.partition_key)
    engine = context.run.tags.get(_ENGINE_TAG)
    return get_review(db, project, filename, engine)


@dg.asset(
    partitions_def=document_partitions,
    group_name="publish",
    retry_policy=_RETRY,
    description=(
        "Chunk approved markdown, or keep it as one chunk, and write tmp/chunks.json. "
        "Text over the chunk threshold is split. Text over the embedding limit "
        "keeps the full wording for keyword search and a shorter semantic summary."
    ),
)
async def chunk_or_skip(
    context: dg.AssetExecutionContext,
    store: LocalDocStore,
    db: DBClient,
    vlm_extractor: VLMExtractor,
) -> dg.MaterializeResult:
    row = _ready_review(context, db)
    project, filename = row["project"], row["filename"]
    if not row["approved"]:
        context.log.info("Parse was rejected")
        return dg.MaterializeResult(metadata={"skipped": True})

    content_dir = _content_dir(store, project, filename)
    markdown = content_dir / const.MARKDOWN_NAME
    if not markdown.is_file():
        raise FileNotFoundError(f"Reviewed markdown not found: {markdown}")
    text = markdown.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError(f"Reviewed markdown is empty: {markdown}")

    # The token number may be updated during review.
    token_num = row["token_num"]
    require_chunking = bool(row["require_chunking"])
    over_threshold = token_num > const.MUST_CHUNK_TOKEN_THRESHOLD
    if require_chunking or over_threshold:
        parts = _chunk_markdown(text)
        if not parts:
            raise ValueError(f"Chunking produced no text for {filename}")
        chunks: list[ChunkText] = [
            {
                "semantic_text": part,
                "keyword_text": part,
                "retrieve_raw_file": False,
            }
            for part in parts
        ]
        chunks_path = _save_chunks(content_dir, chunks)
        context.log.info("Wrote %s chunks to %s", len(chunks), chunks_path)
        return dg.MaterializeResult(
            metadata={"chunks": len(chunks), "token_num": token_num, "chunked": True},
        )

    semantic_text = text
    summarized = False
    if token_num > const.EMBEDDING_TOKEN_LIMIT:
        context.log.info(
            "Summarizing %s for semantic search (%s tokens)", filename, token_num
        )
        semantic_text = await vlm_extractor.extract_summary(
            text=text, logger=context.log.info
        )
        summarized = True
    chunks: list[ChunkText] = [
        {
            "semantic_text": semantic_text,
            "keyword_text": text,
            "retrieve_raw_file": False,
        }
    ]
    chunks_path = _save_chunks(content_dir, chunks)
    context.log.info("Wrote one chunk to %s", chunks_path)
    return dg.MaterializeResult(
        metadata={
            "chunks": 1,
            "token_num": token_num,
            "chunked": False,
            "summarized": summarized,
        },
    )


@dg.asset(
    partitions_def=document_partitions,
    group_name="publish",
    retry_policy=_RETRY,
    description=(
        "When review rejects the parse, describe the original file with the vision model "
        "and write tmp/chunks.json."
    ),
)
async def vlm(
    context: dg.AssetExecutionContext,
    store: LocalDocStore,
    db: DBClient,
    vlm_extractor: VLMExtractor,
) -> dg.MaterializeResult:
    row = _ready_review(context, db)
    project, filename = row["project"], row["filename"]
    if row["approved"]:
        context.log.info("Parse was approved")
        return dg.MaterializeResult(metadata={"skipped": True})

    content_dir = _content_dir(store, project, filename)
    source = _find_parse_source(content_dir, filename)
    summary = await vlm_extractor.extract_summary(
        source=source, logger=context.log.info
    )
    keyword = await vlm_extractor.extract_keyword(source, logger=context.log.info)
    chunks: list[ChunkText] = [
        {
            "semantic_text": summary,
            "keyword_text": keyword,
            "retrieve_raw_file": True,
        }
    ]
    chunks_path = _save_chunks(content_dir, chunks)
    context.log.info("Wrote VLM chunks for %s to %s", source.name, chunks_path)
    return dg.MaterializeResult(
        metadata={"chunks": 1, "source": source.name},
    )


@dg.asset(
    partitions_def=document_partitions,
    group_name="publish",
    deps=[chunk_or_skip, vlm],
    retry_policy=_RETRY,
    description=(
        "Publish files to processed/{project}/{filename}/, store file metadata "
        "in PostgreSQL, and mark the review processed."
    ),
)
def publish_files(
    context: dg.AssetExecutionContext,
    store: LocalDocStore,
    db: DBClient,
) -> dg.MaterializeResult:
    row = _ready_review(context, db)
    project, filename = row["project"], row["filename"]

    # publish the files
    published = _publish_reviewed_files(
        store, project, filename, approved=bool(row["approved"])
    )

    # store the file metadata
    raw = store.raw_file(project, filename)
    size = raw.stat().st_size if raw.is_file() else None
    mime_type = mimetypes.guess_type(filename)[0]
    save_file_metadata(db, project, filename, mime_type, size)

    # mark the review processed
    updated = mark_review_processed(
        db, project, filename, row["engine"], row["create_time"]
    )
    if updated != 1:
        current = get_review(db, project, filename, row["engine"])
        if not (
            current
            and current["processed"]
            and current["create_time"] == row["create_time"]
        ):
            raise dg.Failure(
                f"Review row for {project}/{filename} was not marked processed"
            )

    context.log.info("Published successfully to %s", published)
    return dg.MaterializeResult(
        metadata={
            "path": str(published),
            "approved": bool(row["approved"]),
        },
    )


@dg.asset(
    partitions_def=document_partitions,
    group_name="publish",
    deps=[publish_files],
    retry_policy=_RETRY,
    description=(
        "Ask the search service to index the published chunks. "
        "Rerun this asset alone when the search service was unavailable."
    ),
)
async def index(
    context: dg.AssetExecutionContext,
) -> None:
    project, filename = parse_document_key(context.partition_key)
    await _index_chunks(project, filename)
    context.log.info("Indexed successfully")


publish_document = dg.define_asset_job(
    name="publish_document",
    selection=dg.AssetSelection.groups("publish"),
    description="Chunk or describe a reviewed document, publish the files, and index them.",
)


@dg.sensor(
    job=publish_document,
    minimum_interval_seconds=30,
    default_status=dg.DefaultSensorStatus.RUNNING,
    description=(
        "Start publish_document while processed = false. "
        "publish_files sets processed, so a later index failure is not queued again."
    ),
)
def reviewed_document_sensor(db: DBClient) -> dg.SensorResult | dg.SkipReason:
    rows = list_ready_reviews(db)
    if not rows:
        return dg.SkipReason("No reviewed documents are waiting to be published.")

    partition_keys: list[str] = []
    run_requests: list[dg.RunRequest] = []
    for row in rows:
        key = document_key(row["project"], row["filename"])
        create_time = row["create_time"].isoformat()
        partition_keys.append(key)
        run_requests.append(
            dg.RunRequest(
                partition_key=key,
                run_key=f"{key}:{row['engine']}:{create_time}",
                tags={_ENGINE_TAG: row["engine"]},
            )
        )

    return dg.SensorResult(run_requests=run_requests)
