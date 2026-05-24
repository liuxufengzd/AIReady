import grpc
from common import const
from common.logger import get_logger
from grpc_protos.search import search_pb2, search_pb2_grpc
from search.importer import Importer
from search.retriever import Retriever

logger = get_logger(__name__)


class SearchServiceServicer(search_pb2_grpc.SearchServiceServicer):
    """gRPC Servicer implementing the SearchService interface."""

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
                file_name_to_chunk_ids=chunks_dict,
            )
        except Exception as e:
            logger.exception(f"[{request.project}] Query failed: {e}")
            return search_pb2.QueryResponse(status=search_pb2.INTERNAL, error=str(e))
