from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from supervisor.tools import (
    search_domain_knowledge,
    search_for_image,
)
from supervisor.prompts import AGENT_PROMPT
from supervisor.model.search_context import SearchContext
from supervisor.model.search_state import SearchState
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
import psycopg_pool
from common.util import get_db_uri


async def create_agent_with_pool(llm: BaseChatModel):
    """Create a long-lived agent backed by a connection pool.

    The caller is responsible for closing the pool on shutdown.
    """
    pool = psycopg_pool.AsyncConnectionPool(
        get_db_uri(),
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0},
    )
    await pool.open()

    checkpointer = AsyncPostgresSaver(pool)
    await checkpointer.setup()

    tools = [search_domain_knowledge, search_for_image]
    agent = create_agent(
        llm,
        tools,
        system_prompt=AGENT_PROMPT,
        checkpointer=checkpointer,
        context_schema=SearchContext,
        state_schema=SearchState,
    )
    return agent, pool, checkpointer
