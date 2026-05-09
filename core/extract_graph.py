"""Currently only support small file mode: image, pdf, powerpoint."""

from typing import TypedDict, cast

from core.common.logger import get_logger
from langgraph.types import Command, RetryPolicy
from langgraph.graph import StateGraph, END, START
from langgraph.runtime import Runtime
from core.extract_context import ExtractContext
from core.extractors.image_extractor import ImageExtractor
from core.common.utils import get_llm
from core.common.prompts import REFINE_OCR_TEXT
from langchain_core.messages import HumanMessage
from core.common.utils import read_image, read_pdf, store_metadata
import mimetypes
import shutil
from langgraph.checkpoint.memory import InMemorySaver
from pptxtoimages.tools import PPTXToImageConverter
from core.extractors.pdf_extractor import PDFExtractor
from core.entity.matadata import (
    Metadata,
    ImageMetadata,
    PDFMetadata,
    PptxMetadata,
    PptxPage,
)
from pathlib import Path
import tempfile
import copy
from pydantic import BaseModel
from langchain_core.messages import SystemMessage

logger = get_logger(__name__)


class State(TypedDict):
    # parameters can be configured by the user
    use_vlm: bool
    pages_to_use_vlm: list[int]

    # parameters controlled by the system
    text_for_keyword_search: str
    text_for_semantic_search: str
    is_image: bool
    is_pdf: bool
    is_pptx: bool
    metadata: Metadata
    meta_extension: BaseModel
    temp_dir: Path


class ExtractGraph:
    def __init__(self):
        self.llm = get_llm()
        self.image_extractor = ImageExtractor()
        self.pdf_extractor = PDFExtractor()

    def _dispatch(self, state: State, runtime: Runtime[ExtractContext]) -> dict:
        source = runtime.context.source
        logger.info(f"Dispatching file: {source}")
        if not source.exists():
            raise FileNotFoundError(f"Source file {source} not found")
        is_image = False
        is_pdf = False
        is_pptx = False
        if source.is_file():
            if source.suffix in [
                ".jpg",
                ".jpeg",
                ".png",
            ]:
                is_image = True
                goto = "_extract_image"
            elif source.suffix in [".pdf"]:
                is_pdf = True
                goto = "_extract_pdf"
            elif source.suffix in [".pptx", ".ppt"]:
                is_pptx = True
                goto = "_extract_powerpoint"
            else:
                raise ValueError(f"Unsupported file type: {source.suffix}")
        else:
            raise ValueError(f"Source is not a file: {source}")

        return Command(
            goto=goto,
            update={"is_image": is_image, "is_pdf": is_pdf, "is_pptx": is_pptx},
        )

    async def _extract_image(
        self, state: State, runtime: Runtime[ExtractContext]
    ) -> Command[str]:
        ocr_text = await self.image_extractor.extract_with_ocr(
            runtime.context.source, runtime.context.languages
        )
        goto = "_extract_metadata_extension"
        if state.get("use_vlm"):
            vlm_text = await self.image_extractor.summarize_with_vlm(
                runtime.context.source
            )
            goto = "_refine_keyword_text_with_llm"

        return Command(
            goto=goto,
            update={
                "text_for_keyword_search": ocr_text,
                "text_for_semantic_search": vlm_text
                if state.get("use_vlm")
                else ocr_text,
            },
        )

    async def _extract_pdf(
        self, state: State, runtime: Runtime[ExtractContext]
    ) -> Command[str]:
        ocr_text = await self.pdf_extractor.extract_with_ocr(
            runtime.context.source, runtime.context.languages
        )
        goto = "_extract_metadata_extension"
        if state.get("use_vlm"):
            vlm_text = await self.pdf_extractor.summarize_with_vlm(
                runtime.context.source
            )
            goto = "_refine_keyword_text_with_llm"

        return Command(
            goto=goto,
            update={
                "text_for_keyword_search": ocr_text,
                "text_for_semantic_search": vlm_text
                if state.get("use_vlm")
                else ocr_text,
            },
        )

    async def _extract_powerpoint(
        self, state: State, runtime: Runtime[ExtractContext]
    ) -> Command[str]:
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
        previous_page = ""
        for page_num, image_file in enumerate(image_files, start=1):
            logger.info(f"Processing slide page {page_num}/{len(image_files)}")
            # Extract text with OCR if not using VLM
            ocr_text = await self.image_extractor.extract_with_ocr(
                Path(image_file), runtime.context.languages
            )
            if page_num not in state.get("pages_to_use_vlm", []) and not state.get(
                "use_vlm"
            ):
                metadata.pages.append(
                    PptxPage(
                        page_num=page_num,
                        semantic_text=ocr_text,
                        keyword_text=ocr_text,
                    )
                )
                previous_page = ocr_text
                continue

            # Extract text with VLM
            context = f"This is one page of a PowerPoint presentation. Topic: {Path(source).stem}. Content of it's previous page: {previous_page}"
            summary = await self.image_extractor.extract_with_vlm(
                Path(image_file), context=context
            )
            previous_page = summary
            metadata.pages.append(
                PptxPage(
                    page_num=page_num,
                    semantic_text=summary,
                    keyword_text=ocr_text,
                )
            )
        return Command(
            goto="_refine_keyword_text_with_llm"
            if state.get("pages_to_use_vlm") or state.get("use_vlm")
            else "_extract_metadata_extension",
            update={"metadata": metadata, "temp_dir": Path(temp_dir)},
        )

    async def _refine_keyword_text_with_llm(
        self,
        state: State,
        runtime: Runtime[ExtractContext],
    ) -> dict:
        async def _do_refine(source: Path, is_image: bool, text: str) -> str:
            message = HumanMessage(
                content=[
                    {
                        "type": "text",
                        "text": REFINE_OCR_TEXT.format(ocr_text=text),
                    },
                    read_image(source) if is_image else read_pdf(source),
                ]
            )
            result = await self.llm.ainvoke([message])
            return result.text

        logger.info(f"Refining keyword text with LLM: {runtime.context.source}")
        if state["is_pptx"]:
            # State is immutable, so we need to create a new metadata object
            new_metadata = copy.deepcopy(cast(PptxMetadata, state["metadata"]))
            if state.get("use_vlm"):
                for i in range(len(new_metadata.pages)):
                    text = await _do_refine(
                        state["temp_dir"] / f"slide_{i + 1}.png",
                        True,
                        new_metadata.pages[i].keyword_text,
                    )
                    new_metadata.pages[i].keyword_text = text
            else:
                for i in state.get("pages_to_use_vlm", []):
                    text = await _do_refine(
                        state["temp_dir"] / f"slide_{i}.png",
                        True,
                        new_metadata.pages[i - 1].keyword_text,
                    )
                    new_metadata.pages[i - 1].keyword_text = text
            return {"metadata": new_metadata}

        new_text = await _do_refine(
            runtime.context.source, state["is_image"], state["text_for_keyword_search"]
        )
        return {"text_for_keyword_search": new_text}

    async def _extract_metadata_extension(
        self, state: State, runtime: Runtime[ExtractContext]
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
                    SystemMessage(
                        content="Extract the defined structured information from the given file"
                    ),
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

    def _store_metadata(self, state: State, runtime: Runtime[ExtractContext]) -> None:
        source = runtime.context.source
        project = runtime.context.project
        if state.get("metadata"):
            metadata = state["metadata"]
        elif state["is_image"]:
            metadata = ImageMetadata(
                project=project,
                mime_type=mimetypes.guess_type(source)[0],
                size=source.stat().st_size,
                file_name=source.name,
                semantic_text=state["text_for_semantic_search"],
                keyword_text=state["text_for_keyword_search"],
            )
        elif state.get("is_pdf"):
            metadata = PDFMetadata(
                project=project,
                mime_type=mimetypes.guess_type(source)[0],
                size=source.stat().st_size,
                file_name=source.name,
                semantic_text=state["text_for_semantic_search"],
                keyword_text=state["text_for_keyword_search"],
            )
        else:
            raise ValueError(f"Unsupported file type: {source.suffix}")
            
        if state.get("meta_extension") is not None:
            metadata = metadata.model_copy(
                update={"extension": state["meta_extension"]}
            )
        store_metadata(project, source.name, metadata)

    def _sink_file(self, state: State, runtime: Runtime[ExtractContext]) -> None:
        source = runtime.context.source
        sink = runtime.context.sink
        logger.info(f"Sinking file: {source} to {sink}")
        if state["is_pptx"]:
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
        graph = StateGraph(State, context_schema=ExtractContext)
        (
            graph.add_node(self._dispatch)
            .add_node(self._extract_image, retry_policy=retry_policy)
            .add_node(self._extract_pdf, retry_policy=retry_policy)
            .add_node(self._extract_powerpoint, retry_policy=retry_policy)
            .add_node(self._refine_keyword_text_with_llm, retry_policy=retry_policy)
            .add_node(self._extract_metadata_extension, retry_policy=retry_policy)
            .add_node(self._store_metadata, retry_policy=retry_policy)
            .add_node(self._sink_file, retry_policy=retry_policy)
            .add_edge(START, "_dispatch")
            .add_edge("_refine_keyword_text_with_llm", "_extract_metadata_extension")
            .add_edge("_extract_metadata_extension", "_store_metadata")
            .add_edge("_store_metadata", "_sink_file")
            .add_edge("_sink_file", END)
        )
        return graph.compile(checkpointer=InMemorySaver())
