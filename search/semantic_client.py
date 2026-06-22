import os
from typing import Any

import chromadb
from langchain_chroma import Chroma
from langchain_core.documents import Document
from common.logger import get_logger
from search.common import const
from langchain_google_genai import GoogleGenerativeAIEmbeddings

logger = get_logger(__name__)


class SemanticClient:
    def __init__(self, database: str, container: str):
        self.vectorstore: Chroma = self.create_container_if_not_exists(
            database, container
        )

    async def store(self, documents: list[Document], batch_size: int = 5000) -> None:
        # ChromaDB has a max batch size of 5461, so we batch to avoid the limit
        if batch_size > 5461:
            raise ValueError("Batch size must be less than 5461")
        for i in range(0, len(documents), batch_size):
            batch = documents[i : i + batch_size]
            await self.vectorstore.aadd_documents(batch)
        logger.info(f"[Semantic] Successfully stored {len(documents)} documents")

    @staticmethod
    def _to_chroma_filter(filters: dict[str, Any] | None) -> dict[str, Any] | None:
        """Convert a plain key/value filter dict to ChromaDB's filter syntax.

        ChromaDB requires exactly one top-level operator.  A dict with multiple
        keys must be expressed as ``{"$and": [{"key": {"$eq": val}}, ...]}``.
        """
        if not filters:
            return filters
        if len(filters) == 1:
            key, val = next(iter(filters.items()))
            return {key: {"$eq": val}}
        return {"$and": [{k: {"$eq": v}} for k, v in filters.items()]}

    async def query(
        self,
        query: str,
        *,
        filters: dict[str, Any] = None,
        top_k: int = const.SEMANTIC_TOP_K,
    ) -> list[Document]:
        retriever = self.vectorstore.as_retriever(
            search_kwargs={
                "k": top_k,
                "filter": self._to_chroma_filter(filters),
            }
        )
        documents = await retriever.ainvoke(query)
        logger.info(
            f"[Semantic] Retrieved {len(documents)} documents for query: '{query}'"
        )
        return documents

    async def delete(self, filters: dict[str, Any]):
        await self.vectorstore.adelete(where=self._to_chroma_filter(filters))
        logger.info(f"[Semantic] Deleted documents matching filters: {filters}")

    def create_container_if_not_exists(self, database: str, container: str) -> Chroma:
        host = os.environ.get("CHROMA_HOST", "localhost")
        port = int(os.environ.get("CHROMA_PORT", "9000"))
        admin = chromadb.AdminClient(
            chromadb.Settings(
                chroma_server_host=host,
                chroma_server_http_port=port,
                chroma_api_impl="chromadb.api.fastapi.FastAPI",
            )
        )
        try:
            admin.get_database(database)
        except Exception:
            admin.create_database(database)
            logger.info(f"[Semantic] Created ChromaDB database: {database}")

        return Chroma(
            client=chromadb.HttpClient(host=host, port=port, database=database),
            collection_name=container.lower().replace(" ", "_"),
            embedding_function=GoogleGenerativeAIEmbeddings(
                model=const.EMBEDDING_MODEL_NAME
            ),
        )
