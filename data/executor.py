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
from data.model.matadata import Metadata, Page
from data.model.review_request import ReviewRequest
from data.model.review_response import ReviewResponse
from data.model.final_answer import FinalAnswer


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
    current_page: int = 0
    meta_pages: list[Page] = field(default_factory=list)
    previous_summary: str = ""

    # The thread_id for the currently-running LangGraph invocation
    active_thread_id: str = ""


class Executor:
    """
    Wraps the LangGraph extraction graph and exposes a three-method HITL interface
    that maps 1-to-1 onto the three API endpoints:

      start()              - kick off extraction → first ReviewRequest (text content)
      continue_text_review() - submit text approval + chunking → next ReviewRequest (final answer)
      submit_final_answer()  - submit final answer edits → None (done) or next ReviewRequest
                               for the next PPT slide
    """

    def __init__(self):
        self.graph = ExtractGraph().build()
        self._sessions: dict[str, _Session] = {}

    # ------------------------------------------------------------------
    # Public HITL interface
    # ------------------------------------------------------------------

    async def start(
        self,
        session_id: str,
        project: str,
        source: Path,
        *,
        languages: list[str] = const.DEFAULT_LANGUAGES,
        meta_schema: Type[BaseModel] | None = None,
    ) -> ReviewRequest:
        """
        Validate the source file, initialise a session, and run the graph until
        the first human-review interrupt.

        Returns a ReviewRequest with:
        - ``thread_id``  - must be echoed in every subsequent call
        - ``content``    - raw extracted text for the human to review / edit
        - ``token_num``  - token count so the human can decide on chunking
        """
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

    async def continue_text_review(
        self,
        session_id: str,
        approved: bool,
        text: str | None,
        require_chunking: bool = False,
    ) -> ReviewRequest:
        """
        Resume the graph after the human has reviewed the extracted text.

        The human can:
        - approve or reject the text
        - edit the text (``text`` carries the revised version)
        - request chunking (``require_chunking``)

        The graph will finish processing (metadata extraction, optional
        summarisation) and pause again at the final-answer review.  This
        method returns that second ReviewRequest (containing the full
        FinalAnswer for the human to confirm / edit).
        """
        session = self._sessions[session_id]
        config = {"configurable": {"thread_id": session.active_thread_id}}

        response = ReviewResponse(
            approved=approved,
            content=text,
            require_chunking=require_chunking,
        )
        await self.graph.ainvoke(Command(resume=response), config)

        state = self.graph.get_state(config)
        if not state.next:
            raise RuntimeError(
                f"Expected a final-review interrupt for session '{session_id}' "
                "but the graph completed unexpectedly."
            )
        return self._build_review_request(session_id, state)

    async def submit_final_answer(
        self,
        session_id: str,
        final_answer: FinalAnswer,
    ) -> ReviewRequest | None:
        """
        Resume the graph with the human-approved (and optionally edited) final answer.

        For non-PPT files this finalises the session and returns None.
        For PPT files it accumulates the page result; if more slides remain it
        starts the next slide's graph run and returns its ReviewRequest so the
        frontend can loop through all pages automatically.
        """
        session = self._sessions[session_id]
        config = {"configurable": {"thread_id": session.active_thread_id}}

        response = ReviewResponse(final_answer=final_answer)
        await self.graph.ainvoke(Command(resume=response), config)

        state = self.graph.get_state(config)
        if state.next:
            raise RuntimeError(
                f"Graph paused unexpectedly after final-answer submission "
                f"for session '{session_id}'."
            )

        values = state.values

        if session.is_pptx:
            summary = values["texts_for_semantic_search"][0]
            session.previous_summary = summary
            session.meta_pages.append(
                Page(
                    page_num=session.current_page + 1,
                    semantic_text=summary,
                    keyword_text=values["texts_for_keyword_search"][0],
                )
            )
            session.current_page += 1

            if session.current_page < len(session.images):
                # Start next slide — returns its first ReviewRequest
                return await self._invoke(session_id)

            metadata = Metadata(
                extension=values.get("meta_extension", None),
                pages=session.meta_pages,
            )
        else:
            metadata = Metadata(
                extension=values.get("meta_extension", None),
                semantic_texts=values["texts_for_semantic_search"],
                keyword_texts=values["texts_for_keyword_search"],
            )

        await self._upload_file_and_metadata(session_id, metadata)
        await self._index_file(session_id, metadata)

        del self._sessions[session_id]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _invoke(self, session_id: str) -> ReviewRequest:
        """
        Create a fresh LangGraph thread for the current PPTX page or the whole file,
        run until the first interrupt, and return the resulting ReviewRequest.
        """
        session = self._sessions[session_id]
        thread_id = str(uuid.uuid4())
        session.active_thread_id = thread_id
        config = {"configurable": {"thread_id": thread_id}}

        if session.is_pptx:
            idx = session.current_page
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

    async def _upload_file_and_metadata(
        self, session_id: str, metadata: Metadata
    ) -> None:
        """Persist metadata and upload the raw source file."""
        session = self._sessions[session_id]
        source = session.source

        metadata.project = session.project
        metadata.mime_type = mimetypes.guess_type(source)[0]
        metadata.size = source.stat().st_size
        metadata.file_name = source.name

        logger.info(f"Saving metadata for {source.name}")
        store_metadata(session.project, source.name, metadata)

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

    async def _index_file(self, session_id: str, metadata: Metadata) -> None:
        """Index the file using the search client."""
        session = self._sessions[session_id]
        metadata_file_name = f"{session.source.stem}.json"
        logger.info(f"Indexing '{metadata_file_name}' in project '{session.project}'")
        async with SearchClient(session.project) as client:
            await client.store([metadata_file_name])
