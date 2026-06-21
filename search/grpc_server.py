import grpc
from langchain_core.documents import Document

from common import const
from common.logger import get_logger
from grpc_protos.search import search_pb2, search_pb2_grpc
from search.importer import Importer
from search.retriever import Retriever
from search.semantic_client import SemanticClient

logger = get_logger(__name__)

_CONV_TOPICS_DB = "supervisor"
_CONV_TOPICS_COLLECTION = "conv_topics"


class SearchServiceServicer(search_pb2_grpc.SearchServiceServicer):
    """gRPC Servicer implementing the SearchService interface."""

    def __init__(self) -> None:
        # Singleton SemanticClient for conversation topics.
        # ChromaDB PersistentClient holds file locks, so we reuse one instance.
        self._conv_client = SemanticClient(
            database=_CONV_TOPICS_DB,
            container=_CONV_TOPICS_COLLECTION,
        )

    # ------------------------------------------------------------------
    # Domain knowledge
    # ------------------------------------------------------------------

    async def Store(
        self, request: search_pb2.StoreRequest, _context: grpc.ServicerContext
    ) -> search_pb2.StoreResponse:
        try:
            async with Importer(request.project) as importer:
                await importer.batch(request.source_file_name)
            return search_pb2.StoreResponse(status=search_pb2.OK)
        except FileNotFoundError as e:
            logger.warning(f"[{request.project}] Store failed - file not found: {e}")
            return search_pb2.StoreResponse(status=search_pb2.NOT_FOUND, error=str(e))
        except Exception as e:
            logger.exception(f"[{request.project}] Store failed: {e}")
            return search_pb2.StoreResponse(status=search_pb2.INTERNAL, error=str(e))

    async def Query(
        self, request: search_pb2.QueryRequest, _context: grpc.ServicerContext
    ) -> search_pb2.QueryResponse:
        filters = dict(request.filters) if request.filters else None
        try:
            async with Retriever(const.DATABASE, request.project) as retriever:
                chunks_dict = await retriever.query(request.query, filters=filters)
            return search_pb2.QueryResponse(
                status=search_pb2.OK,
                results=[
                    search_pb2.FileChunks(file_name=k, chunk_ids=v)
                    for k, v in chunks_dict.items()
                ],
            )
        except Exception as e:
            logger.exception(f"[{request.project}] Query failed: {e}")
            return search_pb2.QueryResponse(status=search_pb2.INTERNAL, error=str(e))

    # ------------------------------------------------------------------
    # Conversation history topic management
    # ------------------------------------------------------------------

    async def StoreConvTopics(
        self,
        request: search_pb2.StoreConvTopicsRequest,
        _context: grpc.ServicerContext,
    ) -> search_pb2.StoreConvTopicsResponse:
        try:
            docs = [
                Document(
                    page_content=t.content,
                    metadata={"thread_id": t.thread_id},
                )
                for t in request.topics
            ]
            await self._conv_client.store(docs)
            logger.info("Stored %d conversation topic(s)", len(docs))
            return search_pb2.StoreConvTopicsResponse(status=search_pb2.OK)
        except Exception as e:
            logger.exception("StoreConvTopics failed: %s", e)
            return search_pb2.StoreConvTopicsResponse(
                status=search_pb2.INTERNAL, error=str(e)
            )

    async def QueryConvTopics(
        self,
        request: search_pb2.QueryConvTopicsRequest,
        _context: grpc.ServicerContext,
    ) -> search_pb2.QueryConvTopicsResponse:
        try:
            top_k = request.top_k if request.top_k > 0 else 3
            docs = await self._conv_client.query(
                request.query,
                filters={"thread_id": request.thread_id},
                top_k=top_k,
            )
            return search_pb2.QueryConvTopicsResponse(
                status=search_pb2.OK,
                contents=[doc.page_content for doc in docs],
            )
        except Exception as e:
            logger.exception(
                "QueryConvTopics failed for thread %s: %s", request.thread_id, e
            )
            return search_pb2.QueryConvTopicsResponse(
                status=search_pb2.INTERNAL, error=str(e)
            )

    async def DeleteConvTopics(
        self,
        request: search_pb2.DeleteConvTopicsRequest,
        _context: grpc.ServicerContext,
    ) -> search_pb2.DeleteConvTopicsResponse:
        try:
            await self._conv_client.delete(filters={"thread_id": request.thread_id})
            logger.info("Deleted conversation topics for thread %s", request.thread_id)
            return search_pb2.DeleteConvTopicsResponse(status=search_pb2.OK)
        except Exception as e:
            logger.exception(
                "DeleteConvTopics failed for thread %s: %s", request.thread_id, e
            )
            return search_pb2.DeleteConvTopicsResponse(
                status=search_pb2.INTERNAL, error=str(e)
            )
