import asyncio
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
            # async operations are supported only in ChromaDB Client-Server mode
            # Use asyncio.to_thread for blocking ChromaDB operations
            await asyncio.to_thread(self.vectorstore.add_documents, batch)
        logger.info(f"[Semantic] Successfully stored {len(documents)} documents")

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
                "filter": filters,
            }
        )
        # Use asyncio.to_thread for blocking retriever operations
        documents = await asyncio.to_thread(retriever.invoke, query)
        logger.info(
            f"[Semantic] Retrieved {len(documents)} documents for query: '{query}'"
        )
        return documents

    async def delete(self, filters: dict[str, Any]):
        await asyncio.to_thread(self.vectorstore.delete, where=filters)
        logger.info(f"[Semantic] Deleted documents matching filters: {filters}")

    async def delete_container(self):
        await asyncio.to_thread(self.vectorstore.delete_collection)
        logger.info("[Semantic] Deleted current collection")

    def create_container_if_not_exists(self, database: str, container: str) -> Chroma:
        collection_name = container.lower().replace(" ", "_")
        persist_path = os.path.join(const.ROOT_DIR, "vectorstore", database)

        # This client is intended for local development and testing. For production, prefer a server-backed Chroma instance.
        client = chromadb.PersistentClient(path=persist_path)
        return Chroma(
            client=client,
            collection_name=collection_name,
            embedding_function=GoogleGenerativeAIEmbeddings(
                model=const.EMBEDDING_MODEL_NAME
            ),
        )
