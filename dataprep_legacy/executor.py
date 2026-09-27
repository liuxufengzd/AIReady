from dataclasses import dataclass
from pathlib import Path
from typing import Type
import mimetypes
import shutil
import tempfile
import uuid

from pydantic import BaseModel
from langgraph.types import Command, StateSnapshot

from common.logger import get_logger
from data.common import const
from data.common.office_converter import is_office_document, office_to_pdf
from data.common.utils import store_metadata
from grpc_protos.search.search_client import SearchClient
from data.common.store_paths import (
    layout_path,
    markdown_path,
    processed_artifact_dir,
    promote_review_artifacts,
)
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
    meta_schema: Type[BaseModel] | None
    # PDF rendering for an Office file; the original file for every other type.
    parse_source: Path
    temp_dir: Path | None = None


def _chunk_field(chunk: object, name: str):
    if isinstance(chunk, dict):
        return chunk.get(name)
    return getattr(chunk, name)


class Executor:
    """Wraps the LangGraph extraction graph and exposes HITL interfaces"""

    def __init__(self):
        self.graph = None
        self.pool = None
        self.checkpointer = None
        self._sessions: dict[str, _Session] = {}

    async def _ensure_graph(self):
        """Lazily initialise the graph and connection pool on first use."""
        if self.graph is None:
            self.graph, self.pool, self.checkpointer = await ExtractGraph().build()

    async def close(self):
        """Close the connection pool. Call this on application shutdown."""
        if self.pool is not None:
            await self.pool.close()
            self.pool = None
            self.graph = None
            self.checkpointer = None

    async def _delete_thread(self, thread_id: str) -> None:
        """Remove all checkpoint rows for a completed session thread."""
        if self.checkpointer is None:
            return
        try:
            await self.checkpointer.adelete_thread(thread_id)
        except Exception:
            logger.warning(
                "Failed to delete checkpoints for thread %s", thread_id, exc_info=True
            )

    def _discard_session(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is not None and session.temp_dir is not None:
            shutil.rmtree(session.temp_dir, ignore_errors=True)

    async def start(
        self,
        project: str,
        source: Path,
        *,
        meta_schema: Type[BaseModel] | None = None,
    ) -> ReviewRequest:
        """Validate the source file, initialise a session, and run the graph until the human-review interrupt."""
        await self._ensure_graph()
        if not source.exists():
            raise FileNotFoundError(f"Source file not found: {source}")
        if not source.is_file():
            raise ValueError(f"Source path is not a file: {source}")
        if source.suffix.lower() not in const.SUPPORTED_FILE_TYPES:
            raise ValueError(f"Unsupported file type: {source.suffix}")

        temp_dir: Path | None = None
        parse_source = source
        if is_office_document(source):
            temp_dir = Path(tempfile.mkdtemp(prefix="office-pdf-"))
            try:
                parse_source = office_to_pdf(source, temp_dir)
            except Exception:
                shutil.rmtree(temp_dir, ignore_errors=True)
                raise
            logger.info(f"Converted Office file to PDF: {parse_source}")

        session = _Session(
            project=project,
            source=source,
            meta_schema=meta_schema,
            parse_source=parse_source,
            temp_dir=temp_dir,
        )
        session_id = str(uuid.uuid4())
        self._sessions[session_id] = session
        try:
            return await self._invoke(session_id)
        except Exception:
            self._discard_session(session_id)
            raise

    def _graph_config(self, session_id: str) -> dict:
        session = self._sessions[session_id]
        return {
            "configurable": {
                "thread_id": session_id,
                "project": session.project,
                "source_name": session.source.name,
                "search_meta_schema": session.meta_schema,
            }
        }

    async def continue_after_content_review(
        self,
        session_id: str,
        approved: bool,
        text: str | None,
        require_chunking: bool = False,
        extension: dict | None = None,
    ) -> ReviewRequest | None:
        """Resume the graph after the extracted content is reviewed by the human"""
        await self._ensure_graph()
        session = self._sessions[session_id]
        if approved and text is not None:
            path = markdown_path(session.project, session.source.stem)
            path.write_text(text, encoding="utf-8")
            logger.info(f"Updated review markdown: {path}")

        config = self._graph_config(session_id)
        response = ReviewResponse(
            approved=approved,
            require_chunking=require_chunking,
            extension=extension,
        )
        await self.graph.ainvoke(Command(resume=response), config)

        state = await self.graph.aget_state(config)
        for task in state.tasks:
            if task.interrupts:
                return self._build_review_request(session_id, state)

        return await self._finalize(session_id)

    async def continue_after_extension_review(
        self,
        session_id: str,
        extension: dict | None,
    ) -> None:
        """Resume the graph with the human-reviewed extension"""
        await self._ensure_graph()
        config = self._graph_config(session_id)
        response = ReviewResponse(extension=extension)
        await self.graph.ainvoke(Command(resume=response), config)

        return await self._finalize(session_id)

    async def _finalize(self, session_id: str) -> None:
        """Upload the accepted result and drop the session."""
        session = self._sessions[session_id]
        await self._upload_metadata(session_id)
        await self._upload_file(session_id)
        # We can use message queue for indexing
        await self._index_file(session.project, session.source.name)
        self._discard_session(session_id)
        await self._delete_thread(session_id)

    async def _invoke(self, session_id: str) -> ReviewRequest:
        """Run the graph on the whole file until the human-review interrupt."""
        session = self._sessions[session_id]
        config = self._graph_config(session_id)

        await self.graph.ainvoke(
            {
                "source": session.parse_source,
            },
            config,
        )

        state = await self.graph.aget_state(config)
        return self._build_review_request(session_id, state)

    def _build_review_request(
        self, session_id: str, state: StateSnapshot
    ) -> ReviewRequest:
        """Extract the interrupt value and stamp it with the session_id."""
        for task in state.tasks:
            if task.interrupts:
                request: ReviewRequest = task.interrupts[0].value
                updates: dict = {"session_id": session_id}
                if request.review_type == "content":
                    session = self._sessions[session_id]
                    to_review = markdown_path(session.project, session.source.stem)
                    layout = layout_path(session.project, session.source.stem)
                    updates["content"] = (
                        to_review.read_text(encoding="utf-8")
                        if to_review.is_file()
                        else None
                    )
                    updates["layout"] = (
                        layout.read_bytes() if layout.is_file() else None
                    )
                return request.model_copy(update=updates)
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
        config = {"configurable": {"thread_id": session_id}}
        state = await self.graph.aget_state(config)
        values = state.values

        chunks = [
            Chunk(
                id=str(uuid.uuid4()),
                semantic_text=_chunk_field(chunk, "semantic_text"),
                keyword_text=_chunk_field(chunk, "keyword_text"),
                retrieve_raw_file=bool(_chunk_field(chunk, "retrieve_raw_file")),
            )
            for chunk in values["chunks"]
        ]

        metadata = Metadata(
            project=session.project,
            mime_type=mimetypes.guess_type(source)[0],
            size=source.stat().st_size,
            filename=source.stem,
            chunks=chunks,
            extension=values.get("extension", None),
        )

        logger.info(f"Uploading metadata for {source.name}")
        store_metadata(session.project, metadata)

    async def _upload_file(self, session_id: str) -> None:
        """Promote an accepted parse, and keep a readable raw file when review fell back to it."""
        session = self._sessions[session_id]
        source = session.source
        promoted = promote_review_artifacts(session.project, source.stem)
        if promoted:
            return

        sink = processed_artifact_dir(session.project, source.stem)
        shutil.rmtree(sink, ignore_errors=True)
        sink.mkdir(parents=True)
        shutil.copy(session.parse_source, sink / session.parse_source.name)

    async def _index_file(self, project: str, source_file_name: str) -> None:
        """Index the file using the search client."""
        logger.info(f"Indexing file '{source_file_name}' in project '{project}'")
        async with SearchClient(project) as client:
            await client.store(source_file_name)
