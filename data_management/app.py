"""Data management API and the unified frontend.

GET  /                         The frontend (files, review, ready, assistant).
GET  /projects                 Projects with raw files or pipeline records.

Raw files
GET  /files?path=&q=&state=&sort=&order=&page=&page_size=
                               One page (up to 100) of a directory under
                               store/s3/raw, or of a name search below it.
                               Files carry their pipeline state; ready files
                               are left out.
GET  /files/raw/{path}         The raw file itself.
POST /extractions              Queue raw files that have no state, or failed.

Review
GET  /reviews?project=&pending=&q=&page=&page_size=
                               One page of review rows, optionally for one
                               project or only the undecided ones.
GET  /reviews/{project}/{filename}?engine=
                               Markdown for a file that has not been reviewed.
GET  /reviews/{project}/{filename}/layout?engine=
                               Layout PDF, when one was drawn.
GET  /reviews/{project}/{filename}/assets/{path}?engine=
                               An image or other file beside that markdown.
POST /reviews/{project}/{filename}?engine=
                               Save the decision. Approval also replaces
                               markdown.md with the edited text, then updates
                               token_num, approved, and require_chunking.

Ready documents
GET    /documents?project=&q=&page=&page_size=
                               One page of the published, indexed documents
                               of a project.
GET    /documents/{project}/{filename}
                               The reviewed markdown of one document.
GET    /documents/{project}/{filename}/chunks
                               Its published chunks with token counts.
GET    /documents/{project}/{filename}/assets/{path}
GET    /documents/{project}/{filename}/layout
GET    /documents/{project}/{filename}/source
DELETE /documents/{project}/{filename}
                               Remove it from search and processed, so the raw
                               file returns to having no state.
"""

import mimetypes
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from data_management.dao import close as close_db
from data_management.library import LibraryService, SearchIndexError
from data_management.paging import PAGE_SIZE
from data_management.service import Conflict, ReviewService, TokenCountError
from data_management.store import LAYOUT_NAME

service = ReviewService()
library = LibraryService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    close_db()


app = FastAPI(
    title="Data Management API",
    description="Manage data for ai ready",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
_FRONTEND_HTML = _FRONTEND_DIR / "index.html"
_API_BASE_TOKEN = "__API_BASE_URL__"
_SUPERVISOR_BASE_TOKEN = "__SUPERVISOR_API_URL__"

app.mount("/app", StaticFiles(directory=_FRONTEND_DIR / "app"), name="frontend")

# Service errors become HTTP errors with the message as ``detail``.
_ERROR_STATUS = {
    FileNotFoundError: 404,
    ValueError: 422,
    Conflict: 409,
    TokenCountError: 502,
    SearchIndexError: 502,
}


def _error_handler(status_code: int):
    async def handle(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=status_code, content={"detail": str(exc)})

    return handle


for _error, _status in _ERROR_STATUS.items():
    app.add_exception_handler(_error, _error_handler(_status))


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class Page(BaseModel):
    total: int = Field(description="Items across all pages, after any filter.")
    page: int
    page_size: int


PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=PAGE_SIZE)]


class ReviewRow(BaseModel):
    project: str
    filename: str
    engine: str
    token_num: int
    approved: bool | None
    require_chunking: bool
    processed: bool
    create_time: datetime


class ReviewList(Page):
    rows: list[ReviewRow]


class ReviewDetail(ReviewRow):
    content: str
    has_layout: bool
    ask_chunking: bool = Field(
        description="Whether the reviewer should be asked about chunking. Documents over the token threshold are chunked on publish either way."
    )
    permit_reject: bool


class ReviewDecision(BaseModel):
    approved: bool
    text: str | None = None
    require_chunking: bool = False
    create_time: datetime


class FileEntry(BaseModel):
    name: str
    path: str = Field(description="Path relative to store/s3/raw, '/'-separated.")
    kind: str = Field(description="'dir' or 'file'.")
    modified: datetime
    size: int | None = None
    mime_type: str | None = None
    project: str | None = Field(
        default=None,
        description="Set for raw/{project}/{filename}, the only files the pipeline processes.",
    )
    supported: bool = False
    state: str | None = Field(
        default=None,
        description="extracting, failed, reviewing, indexing, ready, or null.",
    )
    error: str | None = None
    extractable: bool = False


class FileListing(Page):
    path: str
    entries: list[FileEntry] = Field(description="One page, directories first.")
    counts: dict[str, int] = Field(
        description="Files per state before the state filter: 'all', 'none', and each state."
    )


class FileRef(BaseModel):
    project: str
    filename: str


class ExtractionRequest(BaseModel):
    files: list[FileRef] = Field(min_length=1)


class ExtractionResult(FileRef):
    queued: bool
    reason: str | None


class DocumentRow(BaseModel):
    project: str
    filename: str
    engine: str
    token_num: int
    approved: bool = Field(
        description="True when the reviewed markdown was published; false when the vision model described the file instead."
    )
    require_chunking: bool
    create_time: datetime


class ReadyDocument(DocumentRow):
    mime_type: str | None
    size: int | None
    chunk_count: int | None


class ReadyTotals(BaseModel):
    documents: int
    chunks: int
    tokens: int


class ReadyList(Page):
    documents: list[ReadyDocument]
    totals: ReadyTotals = Field(description="Every ready document of the project, before the search.")


class DocumentDetail(DocumentRow):
    content: str | None
    has_layout: bool
    source_name: str | None


class Chunk(BaseModel):
    id: str
    semantic_text: str
    keyword_text: str
    retrieve_raw_file: bool
    semantic_tokens: int
    keyword_tokens: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _api_base_url() -> str:
    configured = os.getenv("API_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    host = os.getenv("HOST", "localhost").strip() or "localhost"
    if host in {"0.0.0.0", "::", "[::]"}:
        host = "localhost"
    port = os.getenv("PORT", "8003").strip() or "8003"
    return f"http://{host}:{port}"


def _supervisor_api_url() -> str:
    return (
        os.getenv("SUPERVISOR_API_URL", "").strip().rstrip("/")
        or "http://localhost:8002"
    )


def _file(path: Path) -> FileResponse:
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
        content_disposition_type="inline",
    )


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def frontend() -> HTMLResponse:
    html = _FRONTEND_HTML.read_text(encoding="utf-8")
    html = html.replace(_API_BASE_TOKEN, _api_base_url())
    html = html.replace(_SUPERVISOR_BASE_TOKEN, _supervisor_api_url())
    return HTMLResponse(html)


@app.get("/projects", response_model=list[str])
def list_projects() -> list[str]:
    return library.projects()


# ---------------------------------------------------------------------------
# Raw files
# ---------------------------------------------------------------------------


@app.get("/files", response_model=FileListing)
def browse_files(
    path: str = "",
    q: str = "",
    state: Literal["all", "none", "extracting", "failed", "reviewing", "indexing"] = "all",
    sort: Literal["name", "type", "size", "modified", "state"] = "name",
    order: Literal["asc", "desc"] = "asc",
    page: PageNumber = 1,
    page_size: PageSize = PAGE_SIZE,
) -> FileListing:
    """One page of a directory under raw, or of every name below it that
    contains ``q``. Ready files are left out."""
    return FileListing.model_validate(
        library.browse(path, q, state, sort, order == "desc", page, page_size)
    )


@app.get("/files/raw/{path:path}")
def raw_file(path: str) -> FileResponse:
    return _file(library.raw_file_path(path))


@app.post("/extractions", response_model=list[ExtractionResult])
def queue_extractions(body: ExtractionRequest) -> list[ExtractionResult]:
    """Queue raw files for dataprep. Files that cannot be queued say why."""
    results = library.queue_extractions(
        [(ref.project, ref.filename) for ref in body.files]
    )
    return [ExtractionResult.model_validate(result) for result in results]


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------


@app.get("/reviews", response_model=ReviewList)
def list_review_files(
    project: str | None = None,
    pending: bool | None = None,
    q: str = "",
    page: PageNumber = 1,
    page_size: PageSize = PAGE_SIZE,
) -> ReviewList:
    """One page of the review table. ``pending=true`` keeps undecided rows."""
    return ReviewList.model_validate(
        service.list_files(project, pending, q, page, page_size)
    )


@app.get("/reviews/{project}/{filename}", response_model=ReviewDetail)
def open_review(project: str, filename: str, engine: str) -> ReviewDetail:
    """Markdown and layout for a file whose review decision is still empty."""
    return ReviewDetail.model_validate(service.open_review(project, filename, engine))


@app.get("/reviews/{project}/{filename}/layout")
def review_layout(project: str, filename: str, engine: str) -> FileResponse:
    """Layout PDF for comparing detected regions with the extracted text."""
    return _file(service.layout_file(project, filename, engine))


@app.get("/reviews/{project}/{filename}/assets/{asset_path:path}")
def review_asset(
    project: str, filename: str, engine: str, asset_path: str
) -> FileResponse:
    """Serve an image referenced by the markdown under review."""
    return _file(service.asset_file(project, filename, engine, asset_path))


@app.post("/reviews/{project}/{filename}", response_model=ReviewRow)
def submit_review(
    project: str, filename: str, engine: str, body: ReviewDecision
) -> ReviewRow:
    """Update token_num, approved, and require_chunking after a review."""
    saved = service.submit_review(
        project,
        filename,
        engine,
        approved=body.approved,
        text=body.text,
        require_chunking=body.require_chunking,
        create_time=body.create_time,
    )
    return ReviewRow.model_validate(saved)


# ---------------------------------------------------------------------------
# Ready documents
# ---------------------------------------------------------------------------


@app.get("/documents", response_model=ReadyList)
def list_ready_documents(
    project: str,
    q: str = "",
    page: PageNumber = 1,
    page_size: PageSize = PAGE_SIZE,
) -> ReadyList:
    """One page of the documents of a project that are published and indexed."""
    return ReadyList.model_validate(
        library.ready_documents(project, q, page, page_size)
    )


@app.get("/documents/{project}/{filename}", response_model=DocumentDetail)
def open_document(project: str, filename: str) -> DocumentDetail:
    return DocumentDetail.model_validate(library.document(project, filename))


@app.get("/documents/{project}/{filename}/chunks", response_model=list[Chunk])
def document_chunks(project: str, filename: str) -> list[Chunk]:
    """Published chunks. Token counts are cached until chunks.json changes."""
    return [Chunk.model_validate(chunk) for chunk in library.chunks(project, filename)]


@app.get("/documents/{project}/{filename}/assets/{asset_path:path}")
def document_asset(project: str, filename: str, asset_path: str) -> FileResponse:
    return _file(library.document_file(project, filename, asset_path))


@app.get("/documents/{project}/{filename}/layout")
def document_layout(project: str, filename: str) -> FileResponse:
    return _file(library.document_file(project, filename, LAYOUT_NAME))


@app.get("/documents/{project}/{filename}/source")
def document_source(project: str, filename: str) -> FileResponse:
    """The PDF or image that was parsed or described."""
    return _file(library.document_source(project, filename))


@app.delete("/documents/{project}/{filename}", status_code=204)
async def delete_document(project: str, filename: str) -> None:
    """Remove a ready document from the index and processed, back to raw."""
    await library.delete_document(project, filename)
