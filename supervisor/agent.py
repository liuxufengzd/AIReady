from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from supervisor.tools import (
    search_domain_knowledge,
    search_for_image,
    search_conversation_history,
)
from supervisor.prompts import AGENT_PROMPT, QUERY_DECOMPOSITION_PROMPT
from supervisor.model.search_context import SearchContext
from supervisor.model.search_state import SearchState
from supervisor.model.query_analysis import QueryAnalysisResult
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langchain.agents.middleware import ToolCallLimitMiddleware
import psycopg_pool
from common.util import get_db_uri


class Agent:
    """Holds all LangChain agents that share a single DB pool and checkpointer."""

    def __init__(self, llm: BaseChatModel) -> None:
        self._llm = llm
        self._pool: psycopg_pool.AsyncConnectionPool | None = None
        self._checkpointer: AsyncPostgresSaver | None = None
        self._rag_agent = None
        self._query_analysis_agent = None

    async def initialize(self) -> None:
        """Set up the shared pool, checkpointer, and all agents."""
        self._pool = psycopg_pool.AsyncConnectionPool(
            get_db_uri(),
            open=False,
            kwargs={"autocommit": True, "prepare_threshold": 0},
        )
        await self._pool.open()

        self._checkpointer = AsyncPostgresSaver(self._pool)
        await self._checkpointer.setup()

    def get_rag_agent(self):
        if self._rag_agent is None:
            self._rag_agent = create_agent(
                self._llm,
                [search_domain_knowledge, search_for_image],
                system_prompt=AGENT_PROMPT,
                checkpointer=self._checkpointer,
                middleware=[
                    ToolCallLimitMiddleware(tool_name="search_for_image", run_limit=3),
                    ToolCallLimitMiddleware(
                        tool_name="search_domain_knowledge", run_limit=3
                    ),
                ],
                context_schema=SearchContext,
                state_schema=SearchState,
            )
        return self._rag_agent

    def get_query_analysis_agent(self):
        if self._query_analysis_agent is None:
            self._query_analysis_agent = create_agent(
                self._llm,
                [search_conversation_history],
                system_prompt=QUERY_DECOMPOSITION_PROMPT,
                checkpointer=self._checkpointer,
                response_format=QueryAnalysisResult,
                context_schema=SearchContext,
            )
        return self._query_analysis_agent

    def get_pool(self) -> psycopg_pool.AsyncConnectionPool:
        return self._pool

    def get_checkpointer(self) -> AsyncPostgresSaver:
        return self._checkpointer

    async def close(self) -> None:
        """Close the shared connection pool."""
        await self._pool.close()
