"""
Search Microservice Entry Point

A gRPC server that provides document retrieval and storage capabilities
using hybrid search (semantic + keyword) with optional reranking.

Usage:
    python -m search.main [--port PORT] [--max-workers MAX_WORKERS]

Environment Variables:
    SEARCH_GRPC_PORT: Port to listen on (default: 50051)
    SEARCH_MAX_WORKERS: Maximum number of worker threads (default: 10)
    LOG_LEVEL: Logging level (default: INFO)
"""

import asyncio
import signal
import os
from concurrent import futures
from pathlib import Path
from dotenv import load_dotenv
import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from grpc_reflection.v1alpha import reflection

from grpc_protos.search import search_pb2, search_pb2_grpc
from search.grpc_server import SearchServiceServicer
from common.logger import get_logger

load_dotenv(Path(__file__).parent / ".env")
logger = get_logger(__name__)

# Configuration
_DEFAULT_PORT = 50051
_DEFAULT_MAX_WORKERS = 10


async def serve() -> None:
    """Start the gRPC server."""
    port = int(os.environ.get("SEARCH_GRPC_PORT", _DEFAULT_PORT))
    max_workers = int(os.environ.get("SEARCH_MAX_WORKERS", _DEFAULT_MAX_WORKERS))

    # Create async gRPC server
    server = grpc.aio.server(
        # The Main Thread: Runs the asyncio event loop. This handles all the networking, coordination, and logic.
        # The Thread Pool: A side-pool of workers (managed by ThreadPoolExecutor) that the main thread "hires" to do chores that would otherwise make the main thread stop and wait.
        # All async tasks are executed by the main thread: The only time your code leaves the main thread is when you explicitly tell Python to move it, like using run_in_executor
        futures.ThreadPoolExecutor(max_workers=max_workers),
        options=[
            # The maximum size (in bytes) of a message the server is allowed to send to the client.
            ("grpc.max_send_message_length", 50 * 1024 * 1024),  # 50MB
            # The maximum size (in bytes) of a message the server is allowed to receive from the client.
            ("grpc.max_receive_message_length", 50 * 1024 * 1024),  # 50MB
        ],
    )

    search_pb2_grpc.add_SearchServiceServicer_to_server(SearchServiceServicer(), server)

    # Add health checking service for Kubernetes liveness/readiness probes
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)

    # The initial overall health of the entire server
    # Kubernetes "Liveness" probes usually check this empty string. If this returns anything other than SERVING, the orchestrator might restart your container.
    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
    # Load balancers use this to decide whether to route specific traffic to this instance.
    # If "Search" is NOT_SERVING, the load balancer will stop sending search queries here but might continue sending other types of requests.
    health_servicer.set("search.SearchService", health_pb2.HealthCheckResponse.SERVING)

    # Enable server reflection for debugging with tools like grpcurl
    # List all services on the server: grpcurl -plaintext localhost:50051 list
    # Describe a specific method: grpcurl -plaintext localhost:50051 describe search.SearchService.Search
    # Disabled in production for security reasons (exposes API structure)
    if os.environ.get("ENABLE_REFLECTION", "false").lower() == "true":
        service_names = (
            search_pb2.DESCRIPTOR.services_by_name["SearchService"].full_name,
            health_pb2.DESCRIPTOR.services_by_name["Health"].full_name,
            reflection.SERVICE_NAME,
        )
        reflection.enable_server_reflection(service_names, server)
        logger.info("gRPC server reflection enabled")

    # Listen on port, [::] means listen on all interfaces(IPv4 + IPv6)
    listen_addr = f"[::]:{port}"
    # creates an unencrypted gRPC channel (no TLS). This is typical for internal Kubernetes services.
    server.add_insecure_port(listen_addr)

    logger.info(f"Starting Search gRPC server on {listen_addr}")
    await server.start()

    # Setup graceful shutdown
    async def graceful_shutdown():
        logger.info("Received shutdown signal, stopping server...")
        await server.stop(grace=5)  # 5 second grace period
        logger.info("Server stopped")

    # Handle shutdown signals — loop.add_signal_handler is Unix-only, so fall
    # back to the standard signal module on Windows.
    loop = asyncio.get_running_loop()
    try:
        # Kubernetes: Pod termination (rolling update, scale down)
        # User: Ctrl+C during local development
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig, lambda: asyncio.create_task(graceful_shutdown())
            )
    except NotImplementedError:
        # Windows does not support loop.add_signal_handler
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda s, f: asyncio.create_task(graceful_shutdown()))

    logger.info("Search gRPC server is running. Press Ctrl+C to stop.")
    await server.wait_for_termination()


def main():
    """Main entry point."""
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        logger.info("Server interrupted by user")
    except Exception as e:
        logger.error(f"Server error: {e}")
        raise


if __name__ == "__main__":
    main()
