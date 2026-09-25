"""Small-file extraction: image, PDF, and Office documents.

An Office file is converted to one PDF before this graph runs, then parsed and
reviewed as a single document.
"""

from typing import Annotated, TypedDict
import operator

from common.logger import get_logger
from langgraph.types import Command, RetryPolicy, interrupt
from langgraph.graph import StateGraph, END, START
from common.util import get_llm
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from common.util import read_file
from data.common.utils import chunk_md
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
import psycopg_pool
from common.util import get_db_uri
from data.model.review_response import ReviewResponse
from pydantic import BaseModel
from langchain_core.messages import SystemMessage
from data.common.prompts import EXTENSION_PROMPT
from data.common import const
from data.common.store_paths import discard_review_artifacts, markdown_path
from data.extractor.mineru_extractor import MineruExtractor
from data.extractor.vlm_extractor import VLMExtractor
from data.model.review_request import ReviewRequest
from pathlib import Path
from typing import Type
from data.model.chunk_detail import ChunkDetail


logger = get_logger(__name__)


class ExtractState(TypedDict):
    source: Path

    # Token count of the parsed markdown.
    token_num: int
    chunks: Annotated[list[ChunkDetail], operator.add]
    extension: dict | None


class ExtractGraph:
    def __init__(self):
        self.llm = get_llm()
        self.mineru_extractor = MineruExtractor()
        self.vlm_extractor = VLMExtractor()

    async def _extract(self, state: ExtractState, config: RunnableConfig) -> dict:
        """Extract with MinerU into the review directory and keep only a token count."""
        source = state.get("source")
        project = config.get("configurable", {}).get("project")
        logger.info(f"Extracting text from {source}")

        await self.mineru_extractor.extract(project, source)
        document = markdown_path(project, source.stem).read_text(encoding="utf-8")
        return {"token_num": self.llm.get_num_tokens(document)}

    async def _review(self, state: ExtractState, config: RunnableConfig) -> dict:
        """Present extracted text for human review.

        The markdown stays on disk. This node records only the token count in
        the interrupt, then reads the file again after the human resumes.
        """
        token_num = state.get("token_num", 0)
        over_chunk_threshold = token_num > const.MUST_CHUNK_TOKEN_THRESHOLD

        review_response: ReviewResponse = interrupt(
            ReviewRequest(
                review_type="content",
                ask_extension=over_chunk_threshold,
                token_num=token_num,
                ask_chunking=not over_chunk_threshold,
            )
        )

        source = state.get("source")
        project = config.get("configurable", {}).get("project")
        if not review_response.approved:
            logger.info("Text rejected by human review, extracting content using VLM")
            discard_review_artifacts(project, source.stem)

            # Extract the summary and keyword using VLM
            summary = await self.vlm_extractor.extract_summary(source)
            keyword = await self.vlm_extractor.extract_keyword(source)
            return Command(
                goto="_extract_metadata_extension",
                update={
                    "chunks": [
                        ChunkDetail(
                            semantic_text=summary,
                            keyword_text=keyword,
                            retrieve_raw_file=True,
                        )
                    ],
                },
            )

        keyword_text = semantic_text = markdown_path(project, source.stem).read_text(
            encoding="utf-8"
        )
        token_num = self.llm.get_num_tokens(keyword_text)
        over_chunk_threshold = token_num > const.MUST_CHUNK_TOKEN_THRESHOLD
        if review_response.require_chunking or over_chunk_threshold:
            chunks = chunk_md(keyword_text)
            logger.info(f"Chunked text into {len(chunks)} chunks")
            return Command(
                goto="_extract_metadata_extension",
                update={
                    "chunks": [
                        ChunkDetail(
                            semantic_text=text,
                            keyword_text=text,
                            retrieve_raw_file=False,
                        )
                        for text in chunks
                    ],
                    "extension": review_response.extension,
                },
            )

        if token_num > const.EMBEDDING_TOKEN_LIMIT:
            logger.info(
                "Summarizing text for vector database because the token number exceeds the limit"
            )
            semantic_text = await self.vlm_extractor.extract_summary(text=semantic_text)
        return Command(
            goto="_extract_metadata_extension",
            update={
                "chunks": [
                    ChunkDetail(
                        semantic_text=semantic_text,
                        keyword_text=keyword_text,
                        retrieve_raw_file=False,
                    )
                ],
                "token_num": token_num,
            },
        )

    async def _extract_metadata_extension(
        self, state: ExtractState, config: RunnableConfig
    ) -> Command[str]:
        """Extract the metadata extension from the file"""
        search_meta_schema: Type[BaseModel] | None = config.get("configurable", {}).get(
            "search_meta_schema"
        )
        if search_meta_schema is not None and not state.get("extension", None):
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
            return Command(
                goto="_review_extension",
                update={"extension": cleaned_data},
            )

        return Command(goto=END)

    def _review_extension(self, state: ExtractState) -> dict:
        """Review the metadata extension"""
        review_response: ReviewResponse = interrupt(
            ReviewRequest(
                review_type="extension", extension=state.get("extension", None)
            )
        )
        return {"extension": review_response.extension}

    async def build(self):
        """Build and compile the graph backed by a PostgreSQL checkpointer.

        Returns a (compiled_graph, pool, checkpointer) tuple.
        The caller owns the pool and must close it on shutdown.
        """
        pool = psycopg_pool.AsyncConnectionPool(
            get_db_uri(),
            open=False,
            kwargs={"autocommit": True, "prepare_threshold": 0},
        )
        await pool.open()

        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()

        retry_policy = RetryPolicy(max_attempts=3)
        graph = StateGraph(ExtractState)
        (
            graph.add_node(self._extract, retry_policy=retry_policy)
            .add_node(self._review, retry_policy=retry_policy)
            .add_node(self._extract_metadata_extension, retry_policy=retry_policy)
            .add_node(self._review_extension, retry_policy=retry_policy)
            .add_edge(START, "_extract")
            .add_edge("_extract", "_review")
            .add_edge("_review_extension", END)
        )
        return graph.compile(checkpointer=checkpointer), pool, checkpointer
