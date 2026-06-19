from langchain.agents import AgentState


class SearchState(AgentState):
    filename_to_chunk_ids: dict[str, list[str]]
