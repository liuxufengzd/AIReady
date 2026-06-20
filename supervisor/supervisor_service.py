import asyncio
import json
import re
import uuid
from urllib.parse import quote
from langchain_core.messages import HumanMessage, ToolMessage
from supervisor.model.search_context import SearchContext
from supervisor.agent import create_agent_with_pool
from common.util import get_llm
from supervisor.prompts import (
    QUERY_DECOMPOSITION_PROMPT,
    SYNTHESIZE_PROMPT,
    PARTIAL_ANSWER_PROMPT,
)
from pydantic import BaseModel, Field
from supervisor.common import const
from common.logger import get_logger
from langfuse.langchain import CallbackHandler
from langgraph.errors import GraphRecursionError


logger = get_logger(__name__)


def _tool_content_to_str(content) -> str:
    """Safely convert ToolMessage content (str, list, or dict) to a plain string."""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False, default=str)
    except Exception:
        return repr(content)


class QuestionAnalysisResult(BaseModel):
    language: str = Field(description="The language of the original question")
    sub_questions: list[str] = Field(description="The list of sub-questions")


class SupervisorService:
    def __init__(self, files_base_url: str = "http://localhost:8002/files") -> None:
        self.files_base_url = files_base_url
        self.agent = None
        self.pool = None
        self.llm = None
        self.checkpointer = None

    async def _ensure_agent(self):
        """Lazily initialise the agent and connection pool on first use."""
        if self.agent is None:
            self.llm = get_llm()
            self.agent, self.pool, self.checkpointer = await create_agent_with_pool(
                self.llm
            )

    async def _delete_thread(self, thread_id: str) -> None:
        """Remove all LangGraph checkpoint data for a single-use thread."""
        if self.checkpointer is None:
            return
        try:
            await self.checkpointer.adelete_thread(thread_id)
        except Exception:
            logger.warning(
                "Failed to delete checkpoints for thread %s", thread_id, exc_info=True
            )

    async def close(self):
        """Close the connection pool. Call this on application shutdown."""
        if self.pool is not None:
            await self.pool.close()
            self.pool = None
            self.agent = None
            self.checkpointer = None

    async def query(
        self,
        project: str,
        query: str,
        filters: dict[str, str] | None = None,
    ) -> str:
        await self._ensure_agent()
        callback_handler = CallbackHandler()
        thread_id = str(uuid.uuid4())

        try:
            return await self._run_query(
                project, query, filters, thread_id, callback_handler
            )
        finally:
            # Threads are ephemeral (one UUID per request, never reused).
            # Delete immediately so checkpoint rows never accumulate.
            await self._delete_thread(thread_id)

    async def _run_query(
        self,
        project: str,
        query: str,
        filters: dict[str, str] | None,
        thread_id: str,
        callback_handler,
    ) -> str:
        # Decompose the query into sub-questions
        result: QuestionAnalysisResult | None = await self.llm.with_structured_output(
            QuestionAnalysisResult
        ).ainvoke(
            QUERY_DECOMPOSITION_PROMPT.format(query=query),
            config={
                "configurable": {"thread_id": thread_id},
                "callbacks": [callback_handler],
            },
        )
        if result is None:
            return "Invalid question. Please check your question and try again."

        # Process the sub-questions in parallel using asyncio.gather
        sub_thread_ids: list[str] = []

        async def process_subquestion(q: str) -> tuple[str, str]:
            sub_thread_id = f"{thread_id}-{uuid.uuid4().hex}"
            sub_thread_ids.append(sub_thread_id)
            config = {
                "configurable": {"thread_id": sub_thread_id},
                "recursion_limit": const.MAX_ITERATIONS,  # limit the number of calling nodes in the agent graph
                "callbacks": [callback_handler],
            }
            try:
                result = await self.agent.ainvoke(
                    {
                        "messages": [HumanMessage(content=f"Query: {q}")],
                        "filename_to_chunk_ids": {},
                    },
                    context=SearchContext(project=project, filters=filters),
                    config=config,
                )

                return (q, result["messages"][-1].content)
            except GraphRecursionError:
                logger.warning("Recursion reached the maximum number of iterations")
                try:
                    state = await self.agent.aget_state(config)
                    messages = state.values.get("messages", [])
                    tool_results = [
                        _tool_content_to_str(msg.content)
                        for msg in messages
                        if isinstance(msg, ToolMessage) and msg.content
                    ]
                    if tool_results:
                        partial_context = "\n\n".join(tool_results)
                        partial_answer = await self.llm.ainvoke(
                            PARTIAL_ANSWER_PROMPT.format(
                                question=q, context=partial_context
                            ),
                            config={"callbacks": [callback_handler]},
                        )
                        return (q, partial_answer.text)
                except Exception:
                    logger.error(
                        "Failed to generate partial answer from collected context",
                        exc_info=True,
                    )
                return (q, "")
            except Exception:
                logger.error(f"Error processing sub-question: {q}", exc_info=True)
                return (q, "")

        # Use asyncio.gather for parallel processing instead of ThreadPoolExecutor
        process_results = await asyncio.gather(
            *[process_subquestion(q) for q in result.sub_questions]
        )

        # Clean up sub-question threads
        await asyncio.gather(
            *[self._delete_thread(tid) for tid in sub_thread_ids],
            return_exceptions=True,
        )

        # Synthesize the answer
        context = "\n---\n".join(
            [f"Question: {q}\nAnswer: {a}" for q, a in process_results]
        )
        synthesized_result = await self.llm.ainvoke(
            SYNTHESIZE_PROMPT.format(
                context=context, query=query, language=result.language
            ),
            config={
                "configurable": {"thread_id": thread_id},
                "callbacks": [callback_handler],
            },
        )

        return self._refine_citations(synthesized_result.text, project)

    def _refine_citations(self, text: str, project: str) -> str:
        citation_map: dict[str, int] = {}
        counter = 0

        def replace(match: re.Match[str]) -> str:
            nonlocal counter
            file_name = match.group(1).strip()
            if file_name not in citation_map:
                counter += 1
                citation_map[file_name] = counter
            return f"[{citation_map[file_name]}]"

        refined = re.sub(r"<agent-citation>(.*?)</agent-citation>", replace, text)

        if citation_map:
            base_url = f"{self.files_base_url}/{quote(project, safe='')}"
            refs = "  \n".join(
                f"[{idx}] [{file_name}]({base_url}/{quote(file_name, safe='')})"
                for file_name, idx in sorted(citation_map.items(), key=lambda x: x[1])
            )
            refined = f"{refined}\n\n{refs}"

        return refined
