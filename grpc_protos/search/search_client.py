"""
gRPC Client for the Search Service.
Provides a convenient interface for other services to communicate with the Search microservice.

Features:
- Client-side load balancing with round-robin policy for headless K8s services
- Automatic reconnection and retry support

传统的 HTTP/1.1 请求是“一问一答”式的,每次请求可能都会新建连接,或者连接用完就释放。普通的 K8s Service (ClusterIP) 是通过 iptables/IPVS 在 L4(传输层) 做负载均衡的。
但 gRPC 基于 HTTP/2,它为了追求极致的性能,客户端和服务器之间只会建立 一条持久的长连接(Long-lived TCP Connection),所有的请求都在这条连接上进行多路复用(Multiplexing)。
这里的 dns:/// 加上 K8s 内部域名表明背后是一个 Headless Service(即普通的 Service 设置了 clusterIP: None)。
普通 Service 与 Headless Service 在 DNS 解析时的区别如下:
普通 Service:DNS 解析只返回一个虚拟的 Cluster IP。
Headless Service:DNS 解析会直接返回后端所有正在运行的 Pod 的真实 IP 列表(A 记录)。
如果不开启客户端负载均衡,gRPC 客户端默认只会拿到解析出来的第一个 Pod IP 并建立长连接,剩下拿到的 IP 列表直接被当成备胎(只有当第一个死掉时才换)。
当你在 gRPC 配置中指定了 dns:///... 方案并开启了 round_robin 策略后,gRPC 客户端在幕后做了以下工作:
定期发起 DNS 轮询:向 K8s DNS 询问 Headless Service 背后所有的 Pod IP。
建立连接池:客户端不再只连接一个 Pod,而是主动与每一个返回的 Pod IP 都建立一条长连接。
应用层分发:当你在代码中调用 await client.query(...) 发送请求时,gRPC 的客户端连接管理器会根据 round_robin(轮转)算法,在它维护的连接池里轮流分发请求(RPC 级别)。
"""

import os
from typing import Any

import grpc

from grpc_protos.search import search_pb2, search_pb2_grpc
from common.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_TARGET = "localhost:50051"

_CHANNEL_OPTIONS = [
    ("grpc.max_send_message_length", 50 * 1024 * 1024),   # 50 MB
    ("grpc.max_receive_message_length", 50 * 1024 * 1024), # 50 MB
    # Round-robin load balancing for headless K8s services
    ("grpc.lb_policy_name", "round_robin"),
    # Enables automatic retry for failed RPCs (e.g., transient network errors).
    ("grpc.enable_retries", 1),
    # Exponential backoff when connection fails
    ("grpc.initial_reconnect_backoff_ms", 100),
    ("grpc.max_reconnect_backoff_ms", 10000),
    # Keepalive to detect dead connections at the application level
    ("grpc.keepalive_time_ms", 30000),
    ("grpc.keepalive_timeout_ms", 10000),
    ("grpc.keepalive_permit_without_calls", 1),
]


class SearchClient:
    """
    gRPC client for the Search microservice.

    Covers two areas of the SearchService API:

    1. **Domain knowledge** – hybrid document retrieval and file indexing.
       These methods require ``project`` to be set on the client.

    2. **Conversation history** – store, query, and delete topic summaries
       extracted from old conversation turns.  These methods accept
       ``thread_id`` as method arguments.

    Usage — domain knowledge:
        async with SearchClient(project="hotel") as client:
            chunks = await client.query("What is the check-in time?")
            await client.store("hotel_policies.pdf")

    Usage — conversation history:
        async with SearchClient() as client:
            await client.store_topics(thread_id, ["Topic: X\\nDetail..."])
            results = await client.query_topics(thread_id, "what about X")
            await client.delete_topics(thread_id)

    Kubernetes with headless service (client-side load balancing):
        async with SearchClient(
            project="hotel",
            target="dns:///search-svc.dataagent.svc.cluster.local:50051"
        ) as client:
            ...
    """

    def __init__(
        self,
        project: str = "",
        target: str | None = None,
    ) -> None:
        """
        Args:
            project: Project name used to scope domain-knowledge operations.
                     Leave empty when only using conversation-history methods.
            target:  gRPC target address.  Supports:
                     - "host:port"  (direct)
                     - "dns:///svc.ns.svc.cluster.local:port"  (K8s headless)
                     Defaults to SEARCH_API_URL env var or "localhost:50051".
        """
        self.project = project
        self.target = target or os.environ.get("SEARCH_API_URL", _DEFAULT_TARGET)
        self._channel: grpc.aio.Channel | None = None
        self._stub: search_pb2_grpc.SearchServiceStub | None = None

    async def __aenter__(self) -> "SearchClient":
        await self._connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self._close()

    # ------------------------------------------------------------------
    # Domain knowledge
    # ------------------------------------------------------------------

    async def query(
        self,
        query: str,
        *,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, list[str]]:
        """Query file chunks from the search service using hybrid search.

        Args:
            query:   Search query text.
            filters: Optional key/value filters to scope the search.

        Returns:
            Dictionary mapping file names to matching chunk IDs.
        """
        stub = self._ensure_connected()
        request = search_pb2.QueryRequest(
            project=self.project,
            query=query,
            filters={k: str(v) for k, v in (filters or {}).items()},
        )
        response: search_pb2.QueryResponse = await stub.Query(request)
        if response.status != search_pb2.OK:
            raise RuntimeError(f"[{self.project}] Query failed: {response.error}")
        return {fc.file_name: list(fc.chunk_ids) for fc in response.results}

    async def store(self, source_file_name: str) -> None:
        """Index a file by name.

        Args:
            source_file_name: Name of the source file to index.
        """
        stub = self._ensure_connected()
        request = search_pb2.StoreRequest(
            project=self.project,
            source_file_name=source_file_name,
        )
        response: search_pb2.StoreResponse = await stub.Store(request)
        if response.status == search_pb2.NOT_FOUND:
            raise FileNotFoundError(f"[{self.project}] Store failed: {response.error}")
        if response.status != search_pb2.OK:
            raise RuntimeError(f"[{self.project}] Store failed: {response.error}")

    # ------------------------------------------------------------------
    # Conversation history
    # ------------------------------------------------------------------

    async def store_topics(self, thread_id: str, contents: list[str]) -> None:
        """Persist conversation topic summaries in the vector DB.

        Args:
            thread_id: Thread the topics belong to (globally unique).
            contents:  Topic texts, e.g. ``["Topic: X\\nDetail..."]``.
        """
        stub = self._ensure_connected()
        topics = [
            search_pb2.ConvTopic(content=c, thread_id=thread_id)
            for c in contents
        ]
        response: search_pb2.StoreConvTopicsResponse = await stub.StoreConvTopics(
            search_pb2.StoreConvTopicsRequest(topics=topics)
        )
        if response.status != search_pb2.OK:
            raise RuntimeError(f"StoreConvTopics failed: {response.error}")

    async def query_topics(
        self, thread_id: str, query: str, top_k: int = 3
    ) -> list[str]:
        """Semantic search over stored topic summaries for a thread.

        Args:
            thread_id: Thread to scope the search to.
            query:     Natural-language search query.
            top_k:     Maximum number of results (default 3).

        Returns:
            Matched topic texts ranked by relevance (may be empty).
        """
        stub = self._ensure_connected()
        response: search_pb2.QueryConvTopicsResponse = await stub.QueryConvTopics(
            search_pb2.QueryConvTopicsRequest(
                thread_id=thread_id, query=query, top_k=top_k
            )
        )
        if response.status != search_pb2.OK:
            raise RuntimeError(f"QueryConvTopics failed: {response.error}")
        return list(response.contents)

    async def delete_topics(self, thread_id: str) -> None:
        """Remove all stored topic summaries for a thread.

        Args:
            thread_id: Thread whose topics should be deleted.
        """
        stub = self._ensure_connected()
        response: search_pb2.DeleteConvTopicsResponse = await stub.DeleteConvTopics(
            search_pb2.DeleteConvTopicsRequest(thread_id=thread_id)
        )
        if response.status != search_pb2.OK:
            raise RuntimeError(f"DeleteConvTopics failed: {response.error}")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _connect(self) -> None:
        if self._channel is None:
            self._channel = grpc.aio.insecure_channel(
                self.target, options=_CHANNEL_OPTIONS
            )
            self._stub = search_pb2_grpc.SearchServiceStub(self._channel)
            logger.info("Connected to Search service at %s", self.target)

    async def _close(self) -> None:
        if self._channel is not None:
            await self._channel.close()
            self._channel = None
            self._stub = None
            logger.info("Disconnected from Search service")

    def _ensure_connected(self) -> search_pb2_grpc.SearchServiceStub:
        if self._stub is None:
            raise RuntimeError(
                "SearchClient not connected. Use it as an async context manager."
            )
        return self._stub
