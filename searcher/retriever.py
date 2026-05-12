import asyncio
from typing import Any
from common.logger import get_logger
from searcher.common import const
from searcher.semantic_client import SemanticClient
from searcher.keyword_client import KeywordClient
from searcher.reranker import Reranker
from langchain_core.documents import Document

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
        rrf_top_k: int = const.RRF_TOP_K,
        rrf_smoothing_constant: int = const.RRF_SMOOTHING_CONSTANT,
        top_k: int = const.TOP_K,
        enable_semantic_search: bool = True,
        enable_keyword_search: bool = True,
        enable_rerank: bool = False,
    ) -> list[Document]:
        if rrf_top_k < top_k:
            raise ValueError("rrf_top_k must be greater than or equal to top_k")
        if rrf_smoothing_constant <= 0:
            raise ValueError("rrf_smoothing_constant must be greater than 0")

        # Collect documents from each retriever in parallel
        docs_semantic: list[Document] = []
        docs_keyword: list[Document] = []

        tasks = []
        if enable_semantic_search:
            tasks.append(self.semantic_client.query(query, filters=filters))
        if enable_keyword_search:
            tasks.append(self.keyword_client.query(query, filters=filters))

        if tasks:
            results = await asyncio.gather(*tasks)
            idx = 0
            if enable_semantic_search:
                docs_semantic = results[idx]
                idx += 1
                logger.debug("======Semantic======")
                for doc in docs_semantic:
                    logger.debug("-" * 100 + "\n" + doc.page_content + "\n" + "-" * 100)

            if enable_keyword_search:
                docs_keyword = results[idx]
                logger.debug("======Keyword=======")
                for doc in docs_keyword:
                    logger.debug("-" * 100 + "\n" + doc.page_content + "\n" + "-" * 100)

        docs: list[Document] = docs_semantic or docs_keyword

        # Fuse and rerank
        if docs_semantic and docs_keyword:
            docs = self.reranker.fuse(
                [docs_semantic, docs_keyword],
                top_k=rrf_top_k,
                smoothing_constant=rrf_smoothing_constant,
            )
        if enable_rerank and docs:
            docs = await self.reranker.rerank(docs, query, top_k=top_k)
            logger.debug("======Reranked======")
        for doc in docs:
            logger.debug("-" * 100 + "\n" + doc.page_content + "\n" + "-" * 100)

        return docs[:top_k]

    async def _close(self) -> None:
        await self.keyword_client.close()
