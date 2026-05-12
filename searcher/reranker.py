import asyncio
import threading
from collections import defaultdict
from langchain_core.documents import Document
from mxbai_rerank.base import RankResult
from searcher.common import const
from common.logger import get_logger
from mxbai_rerank import MxbaiRerankV2


logger = get_logger(__name__)

# Thread-safe singleton for the rerank model
# Note: RLock is used for both initialization AND usage
_model_lock = threading.RLock()
_model_instance: MxbaiRerankV2 | None = None


def _get_shared_model() -> MxbaiRerankV2:
    """Get or create the shared MxbaiRerankV2 model instance (thread-safe)."""
    global _model_instance
    if _model_instance is None:
        with _model_lock:
            # Double-check locking pattern
            if _model_instance is None:
                logger.info("Initializing MxbaiRerankV2 model...")
                _model_instance = MxbaiRerankV2(const.CROSS_ENCODER_MODEL_NAME)
    return _model_instance


def _sync_rerank(
    docs: list[Document], query: str, top_k: int
) -> list[Document]:
    """Synchronous reranking function to be run in executor."""
    if not docs:
        return []

    with _model_lock:
        rank_list: list[RankResult] = _get_shared_model().rank(
            query,
            [doc.page_content for doc in docs],
            top_k=top_k,
            batch_size=const.RERANKER_BATCH_SIZE,
        )

    result = [
        Document(page_content=r.document, metadata=docs[r.index].metadata)
        for r in rank_list
    ]
    logger.info(f"Reranked {len(result)} documents for query: {query}")
    return result


class Reranker:
    def fuse(
        self,
        ranked_lists: list[list[Document]],
        *,
        top_k: int = const.RRF_TOP_K,
        smoothing_constant: int = const.RRF_SMOOTHING_CONSTANT,
    ) -> list[Document]:
        """Apply Reciprocal Rank Fusion to combine multiple ranked lists of documents."""
        rrf_scores: dict[str, float] = defaultdict(float)
        unique_docs: dict[str, Document] = {}

        for ranked_list in ranked_lists:
            for rank, doc in enumerate(ranked_list, 1):
                content = doc.page_content
                if content not in unique_docs:
                    unique_docs[content] = doc
                rrf_scores[content] += 1 / (smoothing_constant + rank)

        sorted_contents = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        return [unique_docs[content] for content, _ in sorted_contents[:top_k]]

    async def rerank(
        self, docs: list[Document], query: str, *, top_k: int = const.TOP_K
    ) -> list[Document]:
        """Perform cross-encoder reranking on a list of documents."""
        if not docs:
            return []

        # Run the GPU-intensive reranking in the default thread pool executor
        # This grabs the central engine that manages all tasks.
        loop = asyncio.get_event_loop()

        # tells the loop: "I have a task that is going to take a long time and isn't built for async. 
        # Please run it in a separate thread so I can keep handling other things."
        # generally use run_in_executor in two specific scenarios:
        # 1. CPU-Bound Tasks: Heavy math, data processing, or machine learning.
        # 2. Blocking I/O: Using a library that doesn't support async (like requests or certain database drivers).
        return await loop.run_in_executor(
            None, _sync_rerank, docs, query, top_k
        )
