import asyncio
import re
import uuid
from langchain.agents import create_agent
from dataclasses import dataclass
from urllib.parse import quote
from supervisor.tools import search_conversation_history
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from langfuse.langchain import CallbackHandler
from pydantic import BaseModel, Field

from common.logger import get_logger
from common.util import get_llm
from supervisor.agent import create_agent_with_pool
from supervisor.common import const
from supervisor.history_extractor import HistoryExtractor
from supervisor.model.search_context import SearchContext
from supervisor.model.thread import QAPair, Thread
from supervisor.prompts import (
    PARTIAL_ANSWER_PROMPT,
    SYNTHESIZE_PROMPT,
    QUERY_DECOMPOSITION_PROMPT,
    TITLE_GENERATION_PROMPT,
)
from supervisor.thread_store import ThreadStore, ThreadSummary

logger = get_logger(__name__)


@dataclass
class QueryResult:
    answer: str
    thread_id: str
    title: str | None


class QuestionAnalysisResult(BaseModel):
    language: str = Field(description="The language of the original question")
    answered_from_context: list[tuple[str, str]] | None = Field(
        default=None,
        description="The list of questions that can be answered from the context. Each item is a tuple of two strings: the question and its answer",
    )
    retrieval_questions: list[str] | None = Field(
        default=None,
        description="The list of questions that require external knowledge retrieval",
    )


class SupervisorService:
    def __init__(self, files_base_url: str = "http://localhost:8002/files") -> None:
        self.files_base_url = files_base_url
        self.agent = None
        self.pool = None
        self.llm = None
        self.checkpointer = None
        self.thread_store: ThreadStore | None = None
        self.history_extractor: HistoryExtractor | None = None
        # Keep strong references to background tasks so they are not GC'd before completion.
        self._background_tasks: set[asyncio.Task] = set()

    async def ensure_initialized(self) -> None:
        """Lazily initialise all resources on first use."""
        if self.agent is None:
            self.llm = get_llm()
            self.agent, self.pool, self.checkpointer = await create_agent_with_pool(
                self.llm
            )
            self.thread_store = ThreadStore(self.pool)
            await self.thread_store.setup()
            self.history_extractor = HistoryExtractor(self.llm)

    async def close(self) -> None:
        """Close resources on application shutdown."""
        # Wait for in-flight background tasks before closing the pool.
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        if self.pool is not None:
            await self.pool.close()
            self.pool = None
            self.agent = None
            self.checkpointer = None
            self.thread_store = None
            self.history_extractor = None

    async def query(
        self,
        project: str,
        query: str,
        thread_id: str | None = None,
        filters: dict[str, str] | None = None,
    ) -> QueryResult:
        await self.ensure_initialized()
        callback_handler = CallbackHandler()

        if thread_id is None:
            thread_id = str(uuid.uuid4())

        thread = await self.thread_store.get_or_create(thread_id, project)

        # Select recent Q&A pairs that fit within the prompt token budget.
        recent_pairs = self._select_recent_pairs(thread)
        history_text = self._format_history(recent_pairs)

        # LangGraph uses its own ephemeral thread IDs (checkpointer rows are
        # deleted immediately after each request).
        lg_thread_id = str(uuid.uuid4())
        try:
            answer = await self._run_query(
                project=project,
                query=query,
                filters=filters,
                lg_thread_id=lg_thread_id,
                callback_handler=callback_handler,
                history_text=history_text,
                conv_thread_id=thread_id,
            )
        finally:
            await self._delete_lg_thread(lg_thread_id)

        # Persist the new Q&A pair.
        qa_tokens = self.llm.get_num_tokens(query + answer)
        new_qa = QAPair(query=query, answer=answer, tokens=qa_tokens)
        await self.thread_store.append_qa(thread_id, new_qa)

        # Generate the thread title on the very first turn.
        title = thread.title
        if title is None:
            title = await self._generate_title(query, answer, callback_handler)
            await self.thread_store.update_title(thread_id, title)

        # Trigger async topic extraction when history has grown beyond the threshold.
        updated_body = thread.body + [new_qa]
        total_tokens = sum(qa.tokens for qa in updated_body)
        if total_tokens > const.THREAD_TRIGGER_TOPIC_EXTRACT_TOKEN_THRESHOLD:
            self._spawn_background(
                self._maybe_extract_topics(
                    thread_id, updated_body, thread.extracted_count
                )
            )

        return QueryResult(answer=answer, thread_id=thread_id, title=title)

    async def list_threads(self, project: str, limit: int = 100) -> list[ThreadSummary]:
        await self.ensure_initialized()
        return await self.thread_store.list_threads(project, limit=limit)

    async def get_thread(self, thread_id: str) -> Thread | None:
        await self.ensure_initialized()
        return await self.thread_store.get(thread_id)

    async def delete_thread(self, thread_id: str) -> None:
        await self.ensure_initialized()
        await self.thread_store.delete_thread(thread_id)
        await self.history_extractor.delete_thread_topics(thread_id)

    async def _run_query(
        self,
        project: str,
        query: str,
        filters: dict[str, str] | None,
        lg_thread_id: str,
        callback_handler,
        history_text: str,
        conv_thread_id: str,
    ) -> str:
        # 1. Decompose the query into atomic sub-questions and try to answer them from the conversation history.
        query_decomposition_agent = create_agent(
            self.llm,
            [search_conversation_history],
            system_prompt=QUERY_DECOMPOSITION_PROMPT,
            checkpointer=self.checkpointer,
            response_format=QuestionAnalysisResult,
        )
        result: QuestionAnalysisResult | None = (
            await query_decomposition_agent.ainvoke(
                {
                    "messages": [
                        HumanMessage(
                            content=[
                                f"Recent conversation history:\n\n {history_text}",
                                f"User query:\n\n {query}",
                            ]
                        ),
                    ]
                },
                context=SearchContext(
                    project=project,
                    thread_id=conv_thread_id,
                ),
                config={
                    "configurable": {"thread_id": lg_thread_id},
                    "callbacks": [callback_handler],
                },
            )
        )["structured_response"]

        if result is None:
            return "Invalid question. Please check your question and try again."

        # Return the answer directly if no retrieval questions are needed.
        if result.retrieval_questions is None:
            return "\n\n".join(a for _, a in result.answered_from_context)

        # 2. Process sub-questions in parallel via the ReAct agent.
        sub_lg_thread_ids: list[str] = []
        process_results: list[tuple[str, str]] = []
        process_results: list[tuple[str, str]] = await asyncio.gather(
            *[
                self._process_subquestion(
                    q=q,
                    language=result.language,
                    lg_thread_id=lg_thread_id,
                    sub_lg_thread_ids=sub_lg_thread_ids,
                    callback_handler=callback_handler,
                    project=project,
                    conv_thread_id=conv_thread_id,
                    filters=filters,
                )
                for q in result.retrieval_questions
            ]
        )

        # Clean up ephemeral LangGraph checkpoints.
        await asyncio.gather(
            *[self._delete_lg_thread(tid) for tid in sub_lg_thread_ids],
            return_exceptions=True,
        )

        # 3. Synthesize the answer, making the answer more precise and informative.
        process_results.extend(result.answered_from_context or [])
        if len(process_results) == 1:
            final_answer = process_results[0][1]
        else:
            context = "\n\n---\n\n".join(
                [f"Question: {q}\n\nAnswer: {a}" for q, a in process_results]
            )
            synthesized_result = await self.llm.ainvoke(
                SYNTHESIZE_PROMPT.format(
                    context=context, query=query, language=result.language
                ),
                config={
                    "configurable": {"thread_id": lg_thread_id},
                    "callbacks": [callback_handler],
                },
            )
            final_answer = synthesized_result.text

        return self._refine_citations(final_answer, project)

    async def _process_subquestion(
        self,
        q: str,
        language: str,
        lg_thread_id: str,
        sub_lg_thread_ids: list[str],
        callback_handler,
        project: str,
        conv_thread_id: str,
        filters: dict[str, str] | None,
    ) -> tuple[str, str]:
        sub_id = f"{lg_thread_id}-{uuid.uuid4().hex}"
        sub_lg_thread_ids.append(sub_id)
        config = {
            "configurable": {"thread_id": sub_id},
            "recursion_limit": const.MAX_ITERATIONS,
            "callbacks": [callback_handler],
        }
        try:
            res = await self.agent.ainvoke(
                {
                    "messages": [
                        HumanMessage(
                            content=f"Answer the question in {language} language: {q}"
                        )
                    ],
                    "filename_to_chunk_ids": {},
                },
                context=SearchContext(
                    project=project,
                    thread_id=conv_thread_id,
                    filters=filters,
                ),
                config=config,
            )
            return (q, res["messages"][-1].text)
        except GraphRecursionError:
            logger.warning("Sub-question exceeded recursion limit: %s", q)
            try:
                state = await self.agent.aget_state(config)
                all_messages = state.values.get("messages", [])
                text_parts: list[str] = []
                multimodal_blocks: list[dict] = []
                for msg in all_messages:
                    if not isinstance(msg, ToolMessage) or not msg.content:
                        continue
                    blocks = (
                        msg.content
                        if isinstance(msg.content, list)
                        else [{"type": "text", "text": str(msg.content)}]
                    )
                    for block in blocks:
                        if block.get("type") == "text":
                            text_parts.append(block["text"])
                        else:
                            multimodal_blocks.append(block)
                if text_parts or multimodal_blocks:
                    text_context = (
                        "\n\n".join(text_parts)
                        if text_parts
                        else "(See attached files below)"
                    )
                    prompt_text = PARTIAL_ANSWER_PROMPT.format(
                        question=q, language=language, context=text_context
                    )
                    human_content: list[dict] = [{"type": "text", "text": prompt_text}]
                    human_content.extend(multimodal_blocks)
                    partial = await self.llm.ainvoke(
                        input=[HumanMessage(content=human_content)],
                        config={"callbacks": [callback_handler]},
                    )
                    return (q, partial.text)
                return (q, "")  # no tool results collected before recursion limit
            except Exception:
                logger.error(
                    "Failed to build partial answer for sub-question: %s",
                    q,
                    exc_info=True,
                )
            return (q, "")
        except Exception:
            logger.error("Error processing sub-question: %s", q, exc_info=True)
            return (q, "")

    @staticmethod
    def _select_recent_pairs(thread: Thread) -> list[QAPair]:
        """Return all Q&A pairs that have not yet been extracted into the vector DB."""
        return thread.body[thread.extracted_count :]

    @staticmethod
    def _format_history(pairs: list[QAPair]) -> str:
        if not pairs:
            return "No previous conversation history"
        return "\n\n---\n\n".join(
            f"User: {qa.query}\nAssistant: {qa.answer}" for qa in pairs
        )

    async def _generate_title(self, query: str, answer: str, callback_handler) -> str:
        try:
            res = await self.llm.ainvoke(
                TITLE_GENERATION_PROMPT.format(query=query, answer=answer),
                config={"callbacks": [callback_handler]},
            )
            return res.text.strip()[:100]
        except Exception:
            logger.warning("Failed to generate thread title", exc_info=True)
            return query[:100]  # fallback: truncated query

    async def _maybe_extract_topics(
        self,
        thread_id: str,
        body: list[QAPair],
        extracted_count: int,
    ) -> None:
        """Background task: compact old Q&A pairs into the vector DB.
        This task only writes topic summaries to vector database so the agent can retrieve them on demand.

        Pairs eligible for extraction: those that are
          (a) not yet extracted  (index >= extracted_count), AND
          (b) outside the recent prompt window (index < recent_start_idx).
        """
        try:
            # Determine where the recent prompt window starts.
            recent_token_sum = 0
            recent_start_idx = len(body)
            for i in range(len(body) - 1, -1, -1):
                if recent_token_sum + body[i].tokens <= const.THREAD_HIST_TOKEN_LIMIT:
                    recent_token_sum += body[i].tokens
                    recent_start_idx = i
                else:
                    break

            pairs_to_extract = body[extracted_count:recent_start_idx]
            if not pairs_to_extract:
                return

            await self.history_extractor.extract_and_store(thread_id, pairs_to_extract)
            new_extracted_count = extracted_count + len(pairs_to_extract)
            await self.thread_store.update_extracted_count(
                thread_id, new_extracted_count
            )
            logger.info(
                "Extracted %d pair(s) for thread %s (cursor: %d → %d)",
                len(pairs_to_extract),
                thread_id,
                extracted_count,
                new_extracted_count,
            )
        except Exception:
            logger.error(
                "Background topic extraction failed for thread %s",
                thread_id,
                exc_info=True,
            )

    async def _delete_lg_thread(self, thread_id: str) -> None:
        if self.checkpointer is None:
            return
        try:
            await self.checkpointer.adelete_thread(thread_id)
        except Exception:
            logger.warning(
                "Failed to delete LangGraph checkpoints for thread %s",
                thread_id,
                exc_info=True,
            )

    def _spawn_background(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

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
