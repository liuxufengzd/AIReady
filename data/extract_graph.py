"""Currently only support small file mode: image, pdf, powerpoint."""

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
from data.extractor.mineru_extractor import MineruExtractor
from data.extractor.llamaparse_extractor import LlamaParseExtractor
from data.extractor.vlm_extractor import VLMExtractor
from data.model.review_request import ReviewRequest
from pathlib import Path
from typing import Type
from data.model.text_pair import TextPair
import shutil


logger = get_logger(__name__)


class ExtractState(TypedDict):
    source: Path
    languages: list[str]
    context: str
    ask_chunking: bool

    # Raw extracted text, written by _extract and read by _review
    extracted_text: str
    first_extraction: bool
    text_pairs: Annotated[list[TextPair], operator.add]
    extension: dict | None


class ExtractGraph:
    def __init__(self):
        self.llm = get_llm()
        self.first_extractor = MineruExtractor()
        self.second_extractor = LlamaParseExtractor()
        self.vlm_extractor = VLMExtractor()

    async def _extract(self, state: ExtractState, config: RunnableConfig) -> dict:
        """Run extraction using the appropriate extractor and store the extracted text in the state."""
        source = state.get("source")
        logger.info(f"Extracting text from {source}")

        first_extraction = state.get("first_extraction", True)
        if first_extraction:
            text = await self.first_extractor.extract(
                project=config.get("configurable", {}).get("project"),
                source=source,
                languages=state.get("languages"),
            )
        else:
            text = await self.second_extractor.extract(source)

        return {
            "extracted_text": text,
            "first_extraction": first_extraction,
        }

    async def _review(self, state: ExtractState, config: RunnableConfig) -> dict:
        """Present extracted text for human review"""
        text = state.get("extracted_text", "")
        token_num = self.llm.get_num_tokens(text)
        over_chunk_threshold = token_num > const.MUST_CHUNK_TOKEN_THRESHOLD
        ask_chunking = state.get("ask_chunking", True)
        if over_chunk_threshold and not ask_chunking:
            raise ValueError("Text is too long, try to split it into separate pages.")

        first_extraction = state.get("first_extraction")

        review_response: ReviewResponse = interrupt(
            ReviewRequest(
                review_type="content",
                content=text,
                ask_extension=over_chunk_threshold,
                token_num=token_num,
                ask_chunking=not over_chunk_threshold and ask_chunking,
                permit_reject=not over_chunk_threshold or first_extraction,
                is_second_extraction=first_extraction is False,
            )
        )

        source = state.get("source")
        image_folder = Path(
            f"store/s3/images/{config.get('configurable', {}).get('project')}/{source.name}"
        )

        if not review_response.approved and first_extraction:
            # Clean the image store folder and start a new extraction
            if image_folder.exists():
                shutil.rmtree(image_folder)
            return Command(goto="_extract", update={"first_extraction": False})

        if review_response.approved or over_chunk_threshold:
            keyword_text = semantic_text = review_response.content
            if review_response.require_chunking or over_chunk_threshold:
                chunks = chunk_md(keyword_text)
                logger.info(f"Chunked text into {len(chunks)} chunks")
                return Command(
                    goto="_extract_metadata_extension",
                    update={
                        "text_pairs": [
                            TextPair(
                                semantic_text=text,
                                keyword_text=text,
                                retrieve_raw_file=False,
                            )
                            for text in chunks
                        ],
                        "extension": review_response.extension,
                    },
                )
            token_num = self.llm.get_num_tokens(keyword_text)
            if token_num > const.EMBEDDING_TOKEN_LIMIT:
                logger.info(
                    "Summarizing text for vector database because the token number exceeds the limit"
                )
                semantic_text = await self.vlm_extractor.extract_summary(
                    text=semantic_text
                )
            return Command(
                goto="_extract_metadata_extension",
                update={
                    "text_pairs": [
                        TextPair(
                            semantic_text=semantic_text,
                            keyword_text=keyword_text,
                            retrieve_raw_file=False,
                        )
                    ],
                },
            )

        logger.info("Text rejected by human review, extracting content using VLM")

        # Clean the image store folder
        if image_folder.exists():
            shutil.rmtree(image_folder)

        # Extract the summary and keyword using VLM
        summary = await self.vlm_extractor.extract_summary(
            source, context=state.get("context", "")
        )
        keyword = await self.vlm_extractor.extract_keyword(source)
        return Command(
            goto="_extract_metadata_extension",
            update={
                "text_pairs": [
                    TextPair(
                        semantic_text=summary,
                        keyword_text=keyword,
                        retrieve_raw_file=True,
                    )
                ],
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
