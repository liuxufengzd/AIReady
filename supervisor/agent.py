from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from supervisor.tools import (
    search_domain_knowledge,
    search_for_image,
)
from supervisor.prompts import AGENT_PROMPT
from supervisor.model.search_context import SearchContext
from langgraph.checkpoint.memory import InMemorySaver
from supervisor.model.search_state import SearchState


def get_agent(llm: BaseChatModel):
    tools = [search_domain_knowledge, search_for_image]

    return create_agent(
        llm,
        tools,
        system_prompt=AGENT_PROMPT,
        checkpointer=InMemorySaver(),  # Only for testing
        context_schema=SearchContext,
        state_schema=SearchState,
    )
