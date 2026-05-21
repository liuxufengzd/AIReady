from data.model.matadata import Metadata
from search.semantic_client import SemanticClient
from search.keyword_client import KeywordClient
from langchain_core.documents import Document
from common.logger import get_logger
from common import const
from search.common import const as search_const
import asyncio

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

    async def batch(self, metadata_file_names: list[str]) -> None:
        metadatas = []
        logger.info(f"[{self.project}] Checking metadata files...")
        for metadata_file_name in metadata_file_names:
            path = (
                search_const.ROOT_DIR
                / "store"
                / "nosql"
                / self.project
                / f"{metadata_file_name}"
            )
            if not path.exists():
                raise FileNotFoundError(f"Metadata file {path} not found")

            with path.open("r", encoding="utf-8") as f:
                metadatas.append(Metadata.model_validate_json(f.read()))

        await asyncio.gather(*[self._index(metadata) for metadata in metadatas])

    async def _index(self, metadata: Metadata) -> None:
        logger.info(f"[{self.project}] Indexing {metadata.file_name}")

        # Delete the indexed documents before indexing of the file
        await self.semantic_client.delete({"_file_name": metadata.file_name})
        await self.keyword_client.delete({"_file_name": metadata.file_name})

        # Index the documents of this file
        index_meta = {"_file_name": metadata.file_name, **(metadata.extension or {})}
        if metadata.pages:
            await asyncio.gather(
                self.semantic_client.store(
                    [
                        Document(
                            page_content=page.semantic_text,
                            metadata={"page": page.page_num, **index_meta},
                        )
                        for page in metadata.pages
                    ]
                ),
                self.keyword_client.store(
                    [
                        Document(
                            page_content=page.keyword_text,
                            metadata={"page": page.page_num, **index_meta},
                        )
                        for page in metadata.pages
                    ]
                ),
            )
        else:
            await asyncio.gather(
                self.semantic_client.store(
                    [
                        Document(page_content=text, metadata=index_meta)
                        for text in metadata.semantic_texts
                    ]
                ),
                self.keyword_client.store(
                    [
                        Document(page_content=text, metadata=index_meta)
                        for text in metadata.keyword_texts
                    ]
                ),
            )

    async def _close(self) -> None:
        await self.keyword_client.close()
