import asyncio
import json
from typing import Any

from langchain_core.documents import Document

from common import const
from common.logger import get_logger
from common.published import PublishedChunk, load_chunks
from search.dao.file_metadata import get_file_metadata
from search.keyword_client import KeywordClient
from search.semantic_client import SemanticClient

logger = get_logger(__name__)

# These keys identify the chunk. An extension field must not replace them.
# "content" is the Elasticsearch text field.
_RESERVED_METADATA_KEYS = {"_file_name", "_chunk_id", "content"}


class Importer:
    def __init__(self, project: str):
        self.semantic_client = SemanticClient(const.DATABASE, project)
        self.keyword_client = KeywordClient(const.DATABASE, project)
        self.project = project

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._close()

    async def batch(self, source_file_name: str) -> None:
        """Index the chunks dataprep published for one source file."""
        logger.info(
            "[%s] Loading published chunks for %s", self.project, source_file_name
        )
        chunks = load_chunks(self.project, source_file_name)
        extension = _file_extension(self.project, source_file_name)
        await self._index(source_file_name, chunks, extension)

    async def _index(
        self,
        filename: str,
        chunks: list[PublishedChunk],
        extension: dict[str, Any],
    ) -> None:
        logger.info("[%s] Indexing %s (%s chunks)", self.project, filename, len(chunks))

        # Replace any previous index for this file.
        await self.semantic_client.delete({"_file_name": filename})
        await self.keyword_client.delete({"_file_name": filename})

        filters = _index_filters(extension)
        semantic_docs: list[Document] = []
        keyword_docs: list[Document] = []
        for chunk in chunks:
            metadata = {
                **filters,
                "_file_name": filename,
                "_chunk_id": chunk.id,
            }
            semantic_docs.append(
                Document(page_content=chunk.semantic_text, metadata=dict(metadata))
            )
            keyword_docs.append(
                Document(page_content=chunk.keyword_text, metadata=dict(metadata))
            )
        await asyncio.gather(
            self.semantic_client.store(semantic_docs),
            self.keyword_client.store(keyword_docs),
        )

    async def _close(self) -> None:
        await self.keyword_client.close()


def _file_extension(project: str, filename: str) -> dict[str, Any]:
    """File-level filter fields saved with the published file."""
    row = get_file_metadata(project, filename)
    if row is None:
        logger.info(
            "[%s] No file metadata for %s; indexing without extension filters",
            project,
            filename,
        )
        return {}
    extension = row.get("extension") or {}
    if isinstance(extension, str):
        extension = json.loads(extension)
    if not isinstance(extension, dict):
        logger.warning("[%s] Ignoring non-object extension for %s", project, filename)
        return {}
    return extension


def _index_filters(
    extension: dict[str, Any],
) -> dict[str, str | int | float | bool]:
    """Keep extension values the vector and keyword indexes can store and filter on."""
    filters: dict[str, str | int | float | bool] = {}
    for key, value in extension.items():
        if not isinstance(key, str) or not key or key in _RESERVED_METADATA_KEYS:
            continue
        if isinstance(value, bool) or isinstance(value, (str, int, float)):
            filters[key] = value
        elif value is not None:
            logger.warning("Skipping non-scalar extension field %s", key)
    return filters
