import asyncio
from typing import Any
from common.logger import get_logger
from search.common import const
from search.semantic_client import SemanticClient
from search.keyword_client import KeywordClient
from search.reranker import Reranker

logger = get_logger(__name__)


class Retriever:
    def __init__(self, database: str, container: str):
        self.semantic_client = SemanticClient(database, container)
        self.keyword_client = KeywordClient(database, container)
        self.reranker = Reranker()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._close()

    async def query(
        self,
        query: str,
        *,
        filters: dict[str, Any] = None,
        top_k: int = const.TOP_K,
    ) -> dict[str, list[str]]:
        tasks = [
            self.semantic_client.query(query, filters=filters),
            self.keyword_client.query(query, filters=filters),
        ]
        results = await asyncio.gather(*tasks)
        chunk_ids_semantic = [
            (doc.metadata.get("_chunk_id"), doc.metadata.get("_file_name"))
            for doc in results[0]
        ]
        chunk_ids_keyword = [
            (doc.metadata.get("_chunk_id"), doc.metadata.get("_file_name"))
            for doc in results[1]
        ]

        chunks = self.reranker.fuse([chunk_ids_semantic, chunk_ids_keyword])[:top_k]

        # create a dict: {file_name: [chunk_ids]}
        chunks_dict: dict[str, list[str]] = {}
        for chunk_id, file_name in chunks:
            if file_name not in chunks_dict:
                chunks_dict[file_name] = []
            chunks_dict[file_name].append(chunk_id)
        return chunks_dict

    async def _close(self) -> None:
        await self.keyword_client.close()
