"""Currently only support small file mode: image, pdf, powerpoint."""

from typing import TypedDict

from data.common.logger import get_logger
from langgraph.types import Command, RetryPolicy
from langgraph.graph import StateGraph, END, START
from langgraph.runtime import Runtime
from data.context import Context
from data.common.utils import get_llm
from langchain_core.messages import HumanMessage
from data.common.utils import read_image, read_pdf, store_metadata
import mimetypes
import shutil
from langgraph.checkpoint.memory import InMemorySaver
from pptxtoimages.tools import PPTXToImageConverter
from data.extractor import Extractor
from data.matadata import (
    Metadata,
    PptxMetadata,
    PptxPage,
)
from pathlib import Path
import tempfile
from pydantic import BaseModel
from langchain_core.messages import SystemMessage
from data.common.prompts import EXTENSION_PROMPT

logger = get_logger(__name__)

SUPPORTED_FILE_TYPES = [".jpg", ".jpeg", ".png", ".pdf", ".pptx", ".ppt"]


class State(TypedDict):
    # parameters can be configured by the user
    use_vlm: bool
    pages_to_use_vlm: list[int]

    # parameters controlled by the system
    text_for_keyword_search: str
    text_for_semantic_search: str
    metadata: Metadata
    meta_extension: BaseModel
    temp_dir: Path
    is_ppt: bool


class Graph:
    def __init__(self):
        self.llm = get_llm()
        self.extractor = Extractor()

    def _preprocess(self, state: State, runtime: Runtime[Context]) -> None:
        source = runtime.context.source
        if not source.exists():
            raise FileNotFoundError(f"Source file {source} not found")
        if not source.is_file():
            raise ValueError(f"Source is not a file: {source}")
        if source.suffix not in SUPPORTED_FILE_TYPES:
            raise ValueError(f"Unsupported file type: {source.suffix}")

    def _dispatch(self, state: State, runtime: Runtime[Context]) -> Command[str]:
        is_ppt = runtime.context.source.suffix in [".pptx", ".ppt"]
        if is_ppt:
            goto = "_extract_ppt"
        else:
            goto = "_extract_image_pdf"
        return Command(goto=goto, update={"is_ppt": is_ppt})

    async def _extract_image_pdf(self, state: State, runtime: Runtime[Context]) -> dict:
        if state.get("use_vlm"):
            summary = await self.extractor.extract_summary_with_llm(
                runtime.context.source
            )
            keyword = await self.extractor.extract_keyword_with_llm(
                runtime.context.source
            )
        else:
            summary = keyword = await self.extractor.extract_with_ocr(
                runtime.context.source, runtime.context.languages
            )

        return {"text_for_keyword_search": keyword, "text_for_semantic_search": summary}

    async def _extract_ppt(self, state: State, runtime: Runtime[Context]) -> dict:
        source = runtime.context.source
        project = runtime.context.project

        # Convert PPTX to images
        temp_dir = tempfile.mkdtemp()
        image_files = PPTXToImageConverter(
            pptx_path=source, output_dir=temp_dir
        ).convert()
        logger.info(f"Converted {len(image_files)} slides to images to '{temp_dir}'")

        metadata = PptxMetadata(
            project=project,
            file_name=source.name,
            mime_type=mimetypes.guess_type(source)[0],
            size=source.stat().st_size,
        )
        previous_summary = ""
        for page_num, image_file in enumerate(image_files, start=1):
            logger.info(f"Processing slide page {page_num}/{len(image_files)}")

            # Extract text with OCR if not using VLM
            if page_num not in state.get("pages_to_use_vlm", []) and not state.get(
                "use_vlm"
            ):
                ocr_text = await self.extractor.extract_with_ocr(
                    Path(image_file), runtime.context.languages
                )
                metadata.pages.append(
                    PptxPage(
                        page_num=page_num,
                        semantic_text=ocr_text,
                        keyword_text=ocr_text,
                    )
                )
                previous_summary = ocr_text
                continue

            # Extract text with VLM
            context = f"This is one page of a PowerPoint presentation. Topic: {Path(source).stem}. Content of it's previous page: {previous_summary}"
            summary = previous_summary = await self.extractor.extract_summary_with_llm(
                Path(image_file), context=context
            )
            keyword = await self.extractor.extract_keyword_with_llm(Path(image_file))
            metadata.pages.append(
                PptxPage(
                    page_num=page_num,
                    semantic_text=summary,
                    keyword_text=keyword,
                )
            )
        return {"metadata": metadata, "temp_dir": Path(temp_dir)}

    async def _extract_metadata_extension(
        self, state: State, runtime: Runtime[Context]
    ) -> dict:
        meta_schema = runtime.context.meta_schema
        result = {}
        if meta_schema is not None:
            source = runtime.context.source
            if state.get("is_pdf"):
                file_content = read_pdf(source)
            else:
                file_content = read_image(source)

            result_obj = await self.llm.with_structured_output(meta_schema).ainvoke(
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
            result = {"meta_extension": meta_schema(**cleaned_data)}

        return result

    def _postprocess(self, state: State, runtime: Runtime[Context]) -> None:
        source = runtime.context.source
        project = runtime.context.project
        sink = runtime.context.sink

        # Store the metadata
        logger.info(f"Storing metadata for {source}")
        if state.get("metadata"):
            metadata = state["metadata"]
        else:
            metadata = Metadata(
                project=project,
                mime_type=mimetypes.guess_type(source)[0],
                size=source.stat().st_size,
                file_name=source.name,
                semantic_text=state["text_for_semantic_search"],
                keyword_text=state["text_for_keyword_search"],
            )

        if state.get("meta_extension") is not None:
            metadata = metadata.model_copy(
                update={"extension": state["meta_extension"]}
            )
        store_metadata(project, source.name, metadata)

        # Sink the raw file to the mark
        logger.info(f"Sinking file: {source} to {sink}")
        if state.get("is_ppt"):
            # PPTX expands into multiple PNG files, so always sink to a directory.
            target_dir = sink if sink.suffix == "" else sink.parent / sink.stem
            target_dir.mkdir(parents=True, exist_ok=True)
            try:
                for file in state["temp_dir"].glob("*.png"):
                    shutil.copy(file, target_dir / file.name)
            finally:
                shutil.rmtree(state["temp_dir"])
        else:
            sink.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(source, sink)

    def build(self):
        retry_policy = RetryPolicy(max_attempts=3)
        graph = StateGraph(State, context_schema=Context)
        (
            graph.add_node(self._preprocess)
            .add_node(self._dispatch)
            .add_node(self._extract_image_pdf, retry_policy=retry_policy)
            .add_node(self._extract_ppt, retry_policy=retry_policy)
            .add_node(self._extract_metadata_extension, retry_policy=retry_policy)
            .add_node(self._postprocess, retry_policy=retry_policy)
            .add_edge(START, "_preprocess")
            .add_edge("_preprocess", "_dispatch")
            .add_edge("_extract_image_pdf", "_extract_metadata_extension")
            .add_edge("_extract_ppt", "_extract_metadata_extension")
            .add_edge("_extract_metadata_extension", "_postprocess")
            .add_edge("_postprocess", END)
        )
        return graph.compile(checkpointer=InMemorySaver())
