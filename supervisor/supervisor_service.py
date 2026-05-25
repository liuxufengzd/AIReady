import asyncio
import uuid
from langchain_core.messages import HumanMessage
from supervisor.search_context import SearchContext
from supervisor.agent import get_agent
from common.util import get_llm
from supervisor.prompts import (
    QUERY_DECOMPOSITION_PROMPT,
    SYNTHESIZE_PROMPT,
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

        return synthesized_result.text
