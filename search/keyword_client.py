import os
from typing import Any
from elasticsearch import AsyncElasticsearch
from langchain_core.documents import Document
from search.common import const
from common.logger import get_logger

logger = get_logger(__name__)


class KeywordClient:
    def __init__(
        self,
        database: str,
        container: str,
    ):
        self.index = f"{database.lower().replace(' ', '_')}_{container.lower().replace(' ', '_')}"
        self.es_host = const.ES_HOST
        self.es_port = const.ES_PORT

        # Initialize async Elasticsearch client
        self.client = AsyncElasticsearch(f"http://{self.es_host}:{self.es_port}")

    async def store(self, documents: list[Document]) -> None:
        if not await self.client.indices.exists(index=self.index):
            await self.create_container_if_not_exists(documents[0].metadata.keys())
        for document in documents:
            await self.client.index(
                index=self.index,
                body={
                    const.TEXT_MAPPING_PROPERTY: document.page_content,
                    **{
                        keyword: document.metadata[keyword]
                        for keyword in document.metadata
                    },
                },
            )
        logger.info(f"[Keyword] Successfully stored {len(documents)} documents")

    async def query(
        self,
        query: str,
        *,
        filters: dict[str, Any] | None = None,
        top_k: int = const.KEYWORD_TOP_K,
    ) -> list[Document]:
        """
        Query the Elasticsearch index for relevant documents.

        Args:
            query: Search query text
            filters: Filters to apply to the search
            top_k: Number of top results to return

        Returns:
            List[Document]: List of relevant documents
        """
        try:
            if not await self.client.indices.exists(index=self.index):
                raise ValueError(f"Index {self.index} does not exist")

            # Construct the search query
            search_body = {
                "query": {
                    "bool": {
                        "must": [
                            {
                                "match": {
                                    const.TEXT_MAPPING_PROPERTY: {
                                        "query": query,
                                        "analyzer": "custom_text_analyzer",
                                    }
                                }
                            }
                        ]
                        + (
                            [
                                {"term": {field: value}}
                                for field, value in filters.items()
                            ]
                            if filters
                            else []
                        ),
                    }
                },
                "size": top_k,
            }

            # Execute the search
            response = await self.client.search(
                index=self.index, body=search_body, ignore_unavailable=True
            )

            # Convert results to Document objects
            documents: list[Document] = []
            for hit in response["hits"]["hits"]:
                source = hit["_source"]
                # Build metadata from all fields except the text field
                metadata = {
                    key: value
                    for key, value in source.items()
                    if key != const.TEXT_MAPPING_PROPERTY
                }
                documents.append(
                    Document(
                        page_content=source[const.TEXT_MAPPING_PROPERTY],
                        metadata=metadata,
                    )
                )

            logger.info(
                f"[Keyword] Retrieved {len(documents)} documents for query: '{query}' in index: {self.index}"
            )
            return documents
        except Exception as e:
            logger.error(
                f"[Keyword] Error querying documents for index {self.index}: {str(e)}"
            )
            return []

    async def delete(self, filters: dict[str, Any]):
        # Construct the final query body
        delete_query = {
            "query": {
                "bool": {
                    "must": [
                        {"term": {field: value}} for field, value in filters.items()
                    ]
                }
            }
        }

        await self.client.delete_by_query(
            index=self.index, body=delete_query, ignore_unavailable=True
        )
        logger.info(f"[Keyword] Deleted documents matching filters: {filters}")

    async def delete_container(self):
        if await self.client.indices.exists(index=self.index):
            await self.client.indices.delete(index=self.index)
            logger.info(f"[Keyword] Deleted existing index: {self.index}")
        else:
            logger.info(f"[Keyword] Index {self.index} does not exist")

    async def create_container_if_not_exists(self, properties: list[str]) -> None:
        """Create Elasticsearch index with appropriate mapping if it doesn't exist.
        Args:
            properties: List of properties to index
        """
        if const.TEXT_MAPPING_PROPERTY in properties:
            raise ValueError(
                f"[Keyword] {const.TEXT_MAPPING_PROPERTY} cannot be indexed"
            )

        if await self.client.indices.exists(index=self.index):
            logger.info(
                f"[Keyword] Index {self.index} already exists, skipping creation"
            )
            return

        # Define synonyms from the synonym.txt file
        synonyms = []
        try:
            synonym_path = os.path.join(const.ROOT_DIR, "search", "synonym.txt")
            if os.path.exists(synonym_path):
                with open(synonym_path, "r", encoding="utf-8") as f:
                    synonyms = [line.strip() for line in f if line.strip()]
        except Exception as e:
            logger.error(f"[Keyword] Error reading synonym file: {e}")

        mapping = {
            "settings": {
                "number_of_shards": const.NUMBER_OF_SHARDS,
                "number_of_replicas": const.NUMBER_OF_REPLICAS,
                "analysis": {
                    "filter": {
                        **(
                            {
                                "synonym_filter": {
                                    "type": "synonym",
                                    "synonyms": synonyms,
                                }
                            }
                            if synonyms
                            else {}
                        ),
                        "english_stop": {"type": "stop", "stopwords": "_english_"},
                    },
                    "analyzer": {
                        "custom_text_analyzer": {
                            "tokenizer": "standard",
                            "filter": [
                                "lowercase",
                                *(["synonym_filter"] if synonyms else []),
                                "porter_stem",
                                "english_stop",
                            ],
                        }
                    },
                },
            },
            "mappings": {
                "properties": {
                    **{property: {"type": "keyword"} for property in properties},
                    const.TEXT_MAPPING_PROPERTY: {
                        "type": "text",
                        "analyzer": "custom_text_analyzer",
                    },
                }
            },
        }

        # Create the index with the corrected body
        try:
            await self.client.indices.create(index=self.index, body=mapping)
            logger.info(f"[Keyword] Created new elasticsearch index: {self.index}")
        except Exception as e:
            logger.error(f"[Keyword] Error creating index {self.index}: {e}")

    async def close(self):
        """Close the Elasticsearch client."""
        await self.client.close()
        logger.info("[Keyword] Elasticsearch client closed.")
