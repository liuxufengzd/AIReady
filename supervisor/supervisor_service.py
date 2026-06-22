import asyncio
import json
import re
import uuid
from collections.abc import AsyncIterator
from urllib.parse import quote
from langchain_core.messages import HumanMessage
from langfuse.langchain import CallbackHandler

from common.logger import get_logger
from common.util import get_llm
from supervisor.agent import Agent
from supervisor.common import const
from supervisor.history_extractor import HistoryExtractor
from supervisor.model.search_context import SearchContext
from supervisor.model.query_analysis import QueryAnalysisResult
from supervisor.model.thread import QAPair, Thread
from supervisor.prompts import (
    SYNTHESIZE_PROMPT,
    TITLE_GENERATION_PROMPT,
)
from supervisor.thread_store import ThreadStore, ThreadSummary

logger = get_logger(__name__)


class SupervisorService:
    def __init__(self, files_base_url: str = "http://localhost:8002/files") -> None:
        self.files_base_url = files_base_url
        self.agent: Agent | None = None
        self.llm = None
        self.thread_store: ThreadStore | None = None
        self.history_extractor: HistoryExtractor | None = None
        # Keep strong references to background tasks so they are not GC'd before completion.
        self._background_tasks: set[asyncio.Task] = set()

    async def _ensure_initialized(self) -> None:
        """Lazily initialise all resources on first use."""
        if self.agent is None:
            self.llm = get_llm()
            self.agent = Agent(self.llm)
            await self.agent.initialize()
            self.thread_store = ThreadStore(self.agent.get_pool())
            await self.thread_store.setup()
            self.history_extractor = HistoryExtractor(self.llm)

    async def close(self) -> None:
        """Close resources on application shutdown."""
        # Wait for in-flight background tasks before closing the pool.
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        if self.agent is not None:
            await self.agent.close()
            self.agent = None
            self.thread_store = None
            self.history_extractor = None

    async def query(
        self,
        project: str,
        query: str,
        thread_id: str | None = None,
        filters: dict[str, str] | None = None,
    ) -> AsyncIterator[str]:
        """
        Stream an answer token-by-token using Server-Sent Events (SSE).

        Event types:
          {"type": "thread_id", "thread_id": "..."}   - sent immediately after setup
          {"type": "chunk",     "text": "..."}         - one or more LLM token chunks
          {"type": "final",     "text": "...", "title": "..."}   - refined answer + title
          {"type": "error",     "message": "..."}      - on failure
        """
        await self._ensure_initialized()
        callback_handler = CallbackHandler()

        if thread_id is None:
            thread_id = str(uuid.uuid4())

        thread = await self.thread_store.get_or_create(thread_id, project)

        yield f"data: {json.dumps({'type': 'thread_id', 'thread_id': thread_id})}\n\n"

        # Select recent Q&A pairs that fit within the prompt token budget.
        recent_pairs = self._select_recent_pairs(thread)
        history_text = self._format_history(recent_pairs)

        # LangGraph uses its own ephemeral thread IDs (checkpointer rows are
        # deleted immediately after each request).
        lg_thread_id = str(uuid.uuid4())

        accumulated: list[str] = []
        stream_error: Exception | None = None
        raw_gen = self._run_query(
            project=project,
            query=query,
            filters=filters,
            lg_thread_id=lg_thread_id,
            callback_handler=callback_handler,
            history_text=history_text,
            conv_thread_id=thread_id,
        )
        try:
            async for chunk in raw_gen:
                accumulated.append(chunk)
                yield f"data: {json.dumps({'type': 'chunk', 'text': chunk})}\n\n"
        except Exception as exc:
            logger.error("Error during streaming query", exc_info=True)
            stream_error = exc
        finally:
            await raw_gen.aclose()
            await self._delete_lg_thread(lg_thread_id)

        if stream_error is not None:
            yield f"data: {json.dumps({'type': 'error', 'message': str(stream_error)})}\n\n"
            return

        full_answer = "".join(accumulated)
        refined_answer = self._refine_citations(full_answer, project)

        qa_tokens = self.llm.get_num_tokens(query + full_answer)
        new_qa = QAPair(query=query, answer=refined_answer, tokens=qa_tokens)
        await self.thread_store.append_qa(thread_id, new_qa)

        # Generate the thread title on the very first turn.
        title = thread.title
        if title is None:
            title = await self._generate_title(query, refined_answer, callback_handler)
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

        yield f"data: {json.dumps({'type': 'final', 'text': refined_answer, 'title': title})}\n\n"

    async def _run_query(
        self,
        project: str,
        query: str,
        filters: dict[str, str] | None,
        lg_thread_id: str,
        callback_handler,
        history_text: str,
        conv_thread_id: str,
    ) -> AsyncIterator[str]:
        """Async generator yielding raw text chunks (without citation refinement)."""
        # Phase 1: query analysis.
        result: QueryAnalysisResult | None = (
            await self.agent.get_query_analysis_agent().ainvoke(
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
            yield "Invalid question. Please check your question and try again."
            return

        if result.retrieval_questions is None:
            yield "\n\n".join(a for _, a in result.answered_from_context)
            return

        # Phase 2: Resolve sub-questions in parallel.
        sub_lg_thread_ids: list[str] = []

        # When there is exactly one retrieval question and no context-answered
        # questions, the agent's output IS the final answer – stream it directly
        # instead of collecting and yielding it all at once.
        is_single_retrieval = (
            len(result.retrieval_questions) == 1 and not result.answered_from_context
        )
        if is_single_retrieval:
            async for chunk in self._process_subquestion_stream(
                user_query=query,
                q=result.retrieval_questions[0],
                language=result.language,
                lg_thread_id=lg_thread_id,
                sub_lg_thread_ids=sub_lg_thread_ids,
                callback_handler=callback_handler,
                project=project,
                conv_thread_id=conv_thread_id,
                filters=filters,
            ):
                yield chunk
            await asyncio.gather(
                *[self._delete_lg_thread(tid) for tid in sub_lg_thread_ids],
                return_exceptions=True,
            )
            return

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

        # Phase 3: Synthesize the answer, making the answer more precise and informative.
        process_results.extend(result.answered_from_context or [])
        context = "\n\n---\n\n".join(
            [f"Question: {q}\n\nAnswer: {a}" for q, a in process_results]
        )
        async for chunk in self.llm.astream(
            SYNTHESIZE_PROMPT.format(
                context=context, query=query, language=result.language
            ),
            config={
                "configurable": {"thread_id": lg_thread_id},
                "callbacks": [callback_handler],
            },
        ):
            yield chunk.text

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
            res = await self.agent.get_rag_agent().ainvoke(
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
        except Exception:
            logger.error("Error processing sub-question: %s", q, exc_info=True)
            return (q, "Error processing this sub-question. Please try again.")

    async def _process_subquestion_stream(
        self,
        user_query: str,
        q: str,
        language: str,
        lg_thread_id: str,
        sub_lg_thread_ids: list[str],
        callback_handler,
        project: str,
        conv_thread_id: str,
        filters: dict[str, str] | None,
    ) -> AsyncIterator[str]:
        sub_id = f"{lg_thread_id}-{uuid.uuid4().hex}"
        sub_lg_thread_ids.append(sub_id)
        config = {
            "configurable": {"thread_id": sub_id},
            "recursion_limit": const.MAX_ITERATIONS,
            "callbacks": [callback_handler],
        }
        try:
            async for chunk in self.agent.get_rag_agent().astream(
                {
                    "messages": [
                        HumanMessage(
                            content=[
                                (
                                    "You are given a user query and a question. "
                                    "Retrieving related information from the knowledge base to answer the question. "
                                    f"Refine your final answer based on the user query in {language} language."
                                ),
                                f"User query: {user_query}",
                                f"Question: {q}",
                            ]
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
                stream_mode="messages",
                version="v2",
            ):
                if chunk["type"] == "messages":
                    token, metadata = chunk["data"]
                    if metadata["langgraph_node"] != "model":
                        continue
                    content_blocks = token.content_blocks
                    for content_block in content_blocks:
                        if content_block["type"] == "text":
                            yield content_block["text"]

        except Exception:
            logger.error("Error streaming sub-question: %s", q, exc_info=True)
            yield "Error processing this sub-question. Please try again."

    async def list_threads(self, project: str, limit: int = 100) -> list[ThreadSummary]:
        await self._ensure_initialized()
        return await self.thread_store.list_threads(project, limit=limit)

    async def get_thread(self, thread_id: str) -> Thread | None:
        await self._ensure_initialized()
        return await self.thread_store.get(thread_id)

    async def delete_thread(self, thread_id: str) -> None:
        await self._ensure_initialized()
        await self.thread_store.delete_thread(thread_id)
        await self.history_extractor.delete_thread_topics(thread_id)

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
        if self.agent is None:
            return
        try:
            await self.agent.get_checkpointer().adelete_thread(thread_id)
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
