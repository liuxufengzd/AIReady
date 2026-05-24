from data.model.matadata import Metadata
from search.semantic_client import SemanticClient
from search.keyword_client import KeywordClient
from langchain_core.documents import Document
from common.logger import get_logger
from common import const
from search.common import const as search_const
import asyncio
from pathlib import Path

logger = get_logger(__name__)


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
        logger.info(f"[{self.project}] Checking metadata file...")
        metadata_file_name = f"{Path(source_file_name).stem}.json"
        path = (
            search_const.ROOT_DIR
            / "store"
            / "postgres"
            / self.project
            / metadata_file_name
        )
        if not path.exists():
            raise FileNotFoundError(f"Metadata file {path} not found")

        with path.open("r", encoding="utf-8") as f:
            metadata = Metadata.model_validate_json(f.read())

        await self._index(metadata)

    async def _index(self, metadata: Metadata) -> None:
        logger.info(f"[{self.project}] Indexing {metadata.file_name}")

        # Delete the indexed file before indexing
        await self.semantic_client.delete({"_file_name": metadata.file_name})
        await self.keyword_client.delete({"_file_name": metadata.file_name})

        # Index the chunks of this file
        tasks = []
        for chunk in metadata.chunks:
            metadata = {
                "_file_name": metadata.file_name,
                "_chunk_id": chunk.id,
                **(metadata.extension or {}),
            }
            tasks.append(
                self.semantic_client.store(
                    [Document(page_content=chunk.semantic_text, metadata=metadata)]
                )
            )
            tasks.append(
                self.keyword_client.store(
                    [Document(page_content=chunk.keyword_text, metadata=metadata)]
                )
            )
        await asyncio.gather(*tasks)

    async def _close(self) -> None:
        await self.keyword_client.close()
