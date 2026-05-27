import asyncio
import re
import uuid
from urllib.parse import quote
from langchain_core.messages import HumanMessage, ToolMessage
from supervisor.search_context import SearchContext
from supervisor.agent import get_agent
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


class QuestionAnalysisResult(BaseModel):
    language: str = Field(description="The language of the original question")
    sub_questions: list[str] = Field(description="The list of sub-questions")


class SupervisorService:
    def __init__(self, files_base_url: str = "http://localhost:8002/files") -> None:
        self.files_base_url = files_base_url

    async def query(
        self,
        project: str,
        query: str,
        filters: dict[str, str] | None = None,
    ) -> str:
        llm = get_llm()
        agent = get_agent(llm)
        callback_handler = CallbackHandler()
        thread_id = str(uuid.uuid4())

        # Decompose the query into sub-questions
        result: QuestionAnalysisResult | None = await llm.with_structured_output(
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
        async def process_subquestion(q: str) -> tuple[str, str]:
            config = {
                "configurable": {"thread_id": thread_id},
                "recursion_limit": const.MAX_ITERATIONS,  # limit the number of calling nodes in the agent graph
                "callbacks": [callback_handler],
            }
            try:
                result = await agent.ainvoke(
                    {
                        "messages": [HumanMessage(content=f"Query: {q}")],
                    },
                    context=SearchContext(project=project, filters=filters),
                    config=config,
                )

                return (q, result["messages"][-1].content)
            except GraphRecursionError:
                logger.warning("Recursion reached the maximum number of iterations")
                try:
                    state = await agent.aget_state(config)
                    messages = state.values.get("messages", [])
                    tool_results = [
                        msg.content
                        for msg in messages
                        if isinstance(msg, ToolMessage) and msg.content
                    ]
                    if tool_results:
                        partial_context = "\n\n".join(tool_results)
                        partial_answer = await llm.ainvoke(
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

        # Synthesize the answer
        context = "\n---\n".join(
            [f"Question: {q}\nAnswer: {a}" for q, a in process_results]
        )
        synthesized_result = await llm.ainvoke(
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
