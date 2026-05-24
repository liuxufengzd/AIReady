from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from supervisor.tools import (
    web_search,
    search_domain_knowledge,
)
from supervisor.prompts import AGENT_PROMPT
from supervisor.search_context import SearchContext
from langgraph.checkpoint.memory import InMemorySaver


def get_agent(llm: BaseChatModel):
    tools = [web_search, search_domain_knowledge]

    return create_agent(
        llm,
        tools,
        system_prompt=AGENT_PROMPT,
        checkpointer=InMemorySaver(),  # Only for testing
        context_schema=SearchContext,
    )
