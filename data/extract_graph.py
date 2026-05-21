"""Currently only support small file mode: image, pdf, powerpoint."""

import os
from typing import TypedDict

from common.logger import get_logger
from langgraph.types import RetryPolicy, interrupt
from langgraph.graph import StateGraph, END, START
from data.common.utils import get_llm
from langchain_core.messages import HumanMessage
from data.common.utils import read_file, chunk_md
from langgraph.checkpoint.memory import InMemorySaver
from data.model.review_response import ReviewResponse
from pydantic import BaseModel
from langchain_core.messages import SystemMessage
from data.common.prompts import EXTENSION_PROMPT
from data.common import const
from data.extractor.mineru_extractor import MineruExtractor
from data.extractor.vlm_extractor import VLMExtractor
from data.model.review_request import ReviewRequest
from data.model.final_answer import FinalAnswer
from pathlib import Path
from typing import Type

logger = get_logger(__name__)


class ExtractState(TypedDict):
    source: Path
    search_meta_schema: Type[BaseModel]
    languages: list[str]
    context: str
    ask_chunking: bool
    # Raw extracted text, written by _extract and read by _review_text
    extracted_text: str
    texts_for_keyword_search: list[str]
    texts_for_semantic_search: list[str]
    meta_extension: BaseModel


class ExtractGraph:
    def __init__(self):
        self.llm = get_llm()
        self.mineru_extractor = MineruExtractor()
        self.vlm_extractor = VLMExtractor()

    async def _extract(self, state: ExtractState) -> dict:
        """Run OCR/Mineru extraction and store the raw text in state.

        This node must NOT contain any interrupt() call so that it is never
        re-executed when the graph is resumed — only _review_text is replayed.
        """
        source = state.get("source")
        logger.info(f"Extracting text from {source}")
        text = await self.mineru_extractor.extract(
            source,
            api_url=os.environ.get("MINERU_API_URL"),
            languages=state.get("languages"),
        )
        return {"extracted_text": text}

    async def _review_text(self, state: ExtractState) -> dict:
        """Present extracted text for human review via interrupt().

        When the graph is resumed this is the only node that is re-executed;
        _extract (the expensive OCR step) is NOT repeated.
        """
        text = state.get("extracted_text", "")
        token_num = self.llm.get_num_tokens(text)
        review_response: ReviewResponse = interrupt(
            ReviewRequest(
                content=text,
                token_num=token_num,
                ask_chunking=state.get("ask_chunking", True),
            )
        )
        if review_response.approved:
            logger.info("Text accepted by human review")
            keyword_text = semantic_text = review_response.content
            if review_response.require_chunking and state.get("ask_chunking", True):
                logger.info("Chunking text as requested by human")
                chunks = chunk_md(keyword_text)
                logger.info(f"Chunked text into {len(chunks)} chunks")
                return {
                    "texts_for_semantic_search": chunks,
                    "texts_for_keyword_search": chunks,
                }
            token_num = self.llm.get_num_tokens(keyword_text)
            if token_num > const.EMBEDDING_TOKEN_LIMIT:
                logger.info(
                    "Summarizing text for vector database because the token number exceeds the limit"
                )
                semantic_text = await self.vlm_extractor.extract_summary(
                    text=keyword_text
                )
            return {
                "texts_for_semantic_search": [semantic_text],
                "texts_for_keyword_search": [keyword_text],
            }

        logger.info("Text rejected by human review, extracting content using VLM")
        source = state.get("source")
        summary = await self.vlm_extractor.extract_summary(
            source, context=state.get("context", "")
        )
        keyword = await self.vlm_extractor.extract_keyword(source)
        return {
            "texts_for_keyword_search": [keyword],
            "texts_for_semantic_search": [summary],
        }

    async def _extract_metadata_extension(self, state: ExtractState) -> dict:
        search_meta_schema = state.get("search_meta_schema")
        if search_meta_schema is not None:
            source = state.get("source")
            logger.info(f"Extracting metadata extension from {source}")
            file_content = read_file(source)

            result_obj = await self.llm.with_structured_output(
                search_meta_schema
            ).ainvoke(
                [
                    SystemMessage(content=EXTENSION_PROMPT),
                    HumanMessage(content=[file_content]),
                ]
            )

            # Safely strip strings without crashing on ints/bools
            data = result_obj.model_dump()
            cleaned_data = {
                k: (v.strip() if isinstance(v, str) else v) for k, v in data.items()
            }
            return {"meta_extension": search_meta_schema(**cleaned_data)}

        return {}

    def _review(self, state: ExtractState) -> dict:
        review_response: ReviewResponse = interrupt(
            ReviewRequest(
                final_answer=FinalAnswer(
                    texts_for_keyword_search=state.get(
                        "texts_for_keyword_search", None
                    ),
                    texts_for_semantic_search=state.get(
                        "texts_for_semantic_search", None
                    ),
                    meta_extension=state.get("meta_extension", None),
                ),
            )
        )
        return {
            "texts_for_keyword_search": review_response.final_answer.texts_for_keyword_search,
            "texts_for_semantic_search": review_response.final_answer.texts_for_semantic_search[
                : const.EMBEDDING_TOKEN_LIMIT
            ],
            "meta_extension": review_response.final_answer.meta_extension,
        }

    def build(self):
        retry_policy = RetryPolicy(max_attempts=3)
        graph = StateGraph(ExtractState)
        (
            graph.add_node(self._extract, retry_policy=retry_policy)
            .add_node(self._review_text, retry_policy=retry_policy)
            .add_node(self._extract_metadata_extension, retry_policy=retry_policy)
            .add_node(self._review, retry_policy=retry_policy)
            .add_edge(START, "_extract")
            .add_edge("_extract", "_review_text")
            .add_edge("_review_text", "_extract_metadata_extension")
            .add_edge("_extract_metadata_extension", "_review")
            .add_edge("_review", END)
        )
        return graph.compile(checkpointer=InMemorySaver())
