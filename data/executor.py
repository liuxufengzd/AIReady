from dataclasses import dataclass, field
from pathlib import Path
from typing import Type
import mimetypes
import shutil
import tempfile
import uuid

from pptxtoimages.tools import PPTXToImageConverter
from pydantic import BaseModel
from langgraph.types import Command, StateSnapshot

from common.logger import get_logger
from data.common import const
from data.common.utils import store_metadata
from grpc_protos.search.search_client import SearchClient
from data.extract_graph import ExtractGraph
from data.model.matadata import Chunk, Metadata
from data.model.review_request import ReviewRequest
from data.model.review_response import ReviewResponse


logger = get_logger(__name__)


@dataclass
class _Session:
    """Tracks all state needed for one end-to-end extraction session."""

    project: str
    source: Path
    languages: list[str]
    meta_schema: Type[BaseModel] | None
    is_pptx: bool

    # PPT-specific
    images: list[Path] = field(default_factory=list)
    temp_dir: Path | None = None
    current_page: int = 1
    previous_summary: str = ""

    # The thread_id for the currently-running LangGraph invocation
    active_thread_id: str = ""


class Executor:
    """Wraps the LangGraph extraction graph and exposes HITL interfaces"""

    def __init__(self):
        self.graph = ExtractGraph().build()
        self._sessions: dict[str, _Session] = {}

    async def start(
        self,
        session_id: str,
        project: str,
        source: Path,
        *,
        languages: list[str] = const.DEFAULT_LANGUAGES,
        meta_schema: Type[BaseModel] | None = None,
    ) -> ReviewRequest:
        """Validate the source file, initialise a session, and run the graph until the human-review interrupt."""
        if not source.exists():
            raise FileNotFoundError(f"Source file not found: {source}")
        if not source.is_file():
            raise ValueError(f"Source path is not a file: {source}")
        if source.suffix not in const.SUPPORTED_FILE_TYPES:
            raise ValueError(f"Unsupported file type: {source.suffix}")

        session = _Session(
            project=project,
            source=source,
            languages=languages,
            meta_schema=meta_schema,
            is_pptx=source.suffix == ".pptx",
        )

        if session.is_pptx:
            tmp = tempfile.mkdtemp()
            images = PPTXToImageConverter(pptx_path=source, output_dir=tmp).convert()
            logger.info(f"Converted {len(images)} slides to images in '{tmp}'")
            session.images = images
            session.temp_dir = Path(tmp)

        self._sessions[session_id] = session
        return await self._invoke(session_id)

    async def continue_after_first_review(
        self,
        session_id: str,
        approved: bool,
        text: str | None,
        extension: BaseModel | None,
        require_chunking: bool = False,
    ) -> ReviewRequest | None:
        """Resume the graph after the first human review"""
        session = self._sessions[session_id]
        config = {"configurable": {"thread_id": session.active_thread_id}}

        response = ReviewResponse(
            approved=approved,
            content=text,
            require_chunking=require_chunking,
            extension=extension,
        )
        await self.graph.ainvoke(Command(resume=response), config)

        state = self.graph.get_state(config)
        for task in state.tasks:
            if task.interrupts:
                return self._build_review_request(session_id, state)

    async def continue_after_extension_review(
        self,
        session_id: str,
        extension: BaseModel,
    ) -> ReviewRequest | None:
        """Resume the graph with the human-reviewed extension"""
        session = self._sessions[session_id]
        config = {"configurable": {"thread_id": session.active_thread_id}}
        response = ReviewResponse(extension=extension)
        await self.graph.ainvoke(Command(resume=response), config)

        # Handle the next slide if the file is a PPTX
        values = self.graph.get_state(config).values
        if session.is_pptx:
            session.previous_summary = values["text_pairs"][-1].semantic_text
            session.current_page += 1
            if session.current_page <= len(session.images):
                # Start to handle the next slide
                return await self._invoke(session_id)

        # Index the file
        await self._index_file(session.project, session.source.name)
        
        # Upload the metadata
        await self._upload_metadata(session_id)

        # Upload the file
        await self._upload_file(session_id)

        # Delete the session
        del self._sessions[session_id]

    async def _invoke(self, session_id: str) -> ReviewRequest:
        """Create a fresh LangGraph thread for the current PPTX page or the whole file, run until the interrupt"""
        session = self._sessions[session_id]
        thread_id = str(uuid.uuid4())
        session.active_thread_id = thread_id
        config = {"configurable": {"thread_id": thread_id}}

        if session.is_pptx:
            idx = session.current_page - 1
            context = f"Slide {idx + 1} of PowerPoint: {session.source.stem}."
            if session.previous_summary:
                context += f" Previous slide summary: {session.previous_summary}"
            logger.info(f"Processing slide {idx + 1}/{len(session.images)}")
            await self.graph.ainvoke(
                {
                    "source": Path(session.images[idx]),
                    "search_meta_schema": session.meta_schema,
                    "languages": session.languages,
                    "context": context,
                    "ask_chunking": False,
                },
                config,
            )
        else:
            await self.graph.ainvoke(
                {
                    "source": session.source,
                    "search_meta_schema": session.meta_schema,
                    "languages": session.languages,
                    "ask_chunking": True,
                },
                config,
            )

        state = self.graph.get_state(config)
        return self._build_review_request(session_id, state)

    def _build_review_request(
        self, session_id: str, state: StateSnapshot
    ) -> ReviewRequest:
        """Extract the interrupt value and stamp it with the session_id."""
        for task in state.tasks:
            if task.interrupts:
                request: ReviewRequest = task.interrupts[0].value
                return request.model_copy(update={"session_id": session_id})
        raise RuntimeError(
            f"Expected an interrupt but none found for session '{session_id}'"
        )

    async def _upload_metadata(
        self,
        session_id: str,
    ) -> None:
        """Combine and upload the metadata."""
        session = self._sessions[session_id]
        source = session.source
        config = {"configurable": {"thread_id": session.active_thread_id}}
        values = self.graph.get_state(config).values

        chunks = [
            Chunk(
                id=str(uuid.uuid4()),
                page_num=session.current_page if session.is_pptx else None,
                semantic_text=text_pair.semantic_text,
                keyword_text=text_pair.keyword_text,
                retrieve_raw_file=values.get("retrieve_raw_file", False),
            )
            for text_pair in values["text_pairs"]
        ]

        metadata = Metadata(
            project=session.project,
            mime_type=mimetypes.guess_type(source)[0],
            size=source.stat().st_size,
            file_name=source.name,
            chunks=chunks,
            extension=values.get("extension", None),
        )

        logger.info(f"Uploading metadata for {source.name}")
        store_metadata(session.project, metadata)

    async def _upload_file(self, session_id: str) -> None:
        """Upload the raw source file."""
        session = self._sessions[session_id]
        source = session.source
        sink = Path(f"store/s3/processed/{session.project}/{source.name}")
        logger.info(f"Uploading raw file: {source} → {sink}")
        if session.is_pptx and session.temp_dir:
            target_dir = sink.parent / sink.stem
            target_dir.mkdir(parents=True, exist_ok=True)
            for file in session.temp_dir.glob("*.png"):
                shutil.copy(file, target_dir / file.name)
            # delete the temp directory
            shutil.rmtree(session.temp_dir)
        else:
            sink.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(source, sink)

    async def _index_file(self, project: str, source_file_name: str) -> None:
        """Index the file using the search client."""
        logger.info(f"Indexing file '{source_file_name}' in project '{project}'")
        async with SearchClient(project) as client:
            await client.store(source_file_name)
