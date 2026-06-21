"""Conversation history compaction.

When a thread's Q&A history grows beyond the trigger threshold, the oldest
pairs are summarised into (topic, detail) entries and stored in the vector
DB via the Search gRPC service, so the agent can retrieve them on demand
through the ``search_conversation_history`` tool.
"""

import os

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field

from common.logger import get_logger
from grpc_protos.search.search_client import SearchClient
from supervisor.model.thread import QAPair
from supervisor.prompts import TOPIC_EXTRACTION_PROMPT

logger = get_logger(__name__)


class _Topic(BaseModel):
    topic: str = Field(description="Concise topic name (1-10 words)")
    detail: str = Field(
        description="Comprehensive summary of all information discussed about this topic (1-10 sentences)"
    )


class _TopicExtractionResult(BaseModel):
    topics: list[_Topic] = Field(
        description="Mutually exclusive and collectively exhaustive topics extracted from the conversation"
    )


class HistoryExtractor:
    """Compacts old Q&A pairs into semantic-searchable topic summaries
    stored in the Search service's vector DB."""

    def __init__(self, llm: BaseChatModel) -> None:
        self.llm = llm
        self._target: str | None = os.environ.get("SEARCH_API_URL")

    async def extract_and_store(self, thread_id: str, qa_pairs: list[QAPair]) -> None:
        """Extract topics from qa_pairs and persist them via the Search service."""
        if not qa_pairs:
            return

        qa_text = "\n\n".join(f"Q: {qa.query}\nA: {qa.answer}" for qa in qa_pairs)

        try:
            result: _TopicExtractionResult = await self.llm.with_structured_output(
                _TopicExtractionResult
            ).ainvoke(TOPIC_EXTRACTION_PROMPT.format(qa_pairs=qa_text))
        except Exception:
            logger.error(
                "LLM topic extraction failed for thread %s", thread_id, exc_info=True
            )
            return

        if not result or not result.topics:
            logger.warning("No topics extracted for thread %s", thread_id)
            return

        contents = [f"Topic: {t.topic}\n{t.detail}" for t in result.topics]

        try:
            async with SearchClient(target=self._target) as client:
                await client.store_topics(thread_id, contents)
            logger.info(
                "Stored %d topic(s) for thread %s via Search service",
                len(contents),
                thread_id,
            )
        except Exception:
            logger.error(
                "Failed to store topics via Search service for thread %s",
                thread_id,
                exc_info=True,
            )

    async def delete_thread_topics(self, thread_id: str) -> None:
        """Remove all stored topic summaries for a thread via the Search service."""
        try:
            async with SearchClient(target=self._target) as client:
                await client.delete_topics(thread_id)
            logger.info(
                "Deleted topic summaries for thread %s via Search service", thread_id
            )
        except Exception:
            logger.warning(
                "Failed to delete topic summaries for thread %s",
                thread_id,
                exc_info=True,
            )
