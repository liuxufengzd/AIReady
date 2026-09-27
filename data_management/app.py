"""Review files recorded in the review table.

GET  /                         The review UI.
GET  /reviews                  Every row in the review table.
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
"""

import mimetypes
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from data_management.service import ReviewConflict, ReviewService, TokenCountError

service = ReviewService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    service.close()


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

_FRONTEND_HTML = (
    Path(__file__).resolve().parent.parent / "frontend" / "data_management" / "index.html"
)
_API_BASE_TOKEN = "__API_BASE_URL__"


class ReviewRow(BaseModel):
    project: str
    filename: str
    engine: str
    token_num: int
    approved: bool | None
    require_chunking: bool
    processed: bool
    create_time: datetime


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


def _api_base_url() -> str:
    configured = os.getenv("API_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    host = os.getenv("HOST", "localhost").strip() or "localhost"
    if host in {"0.0.0.0", "::", "[::]"}:
        host = "localhost"
    port = os.getenv("PORT", "8003").strip() or "8003"
    return f"http://{host}:{port}"


def _call(fn):
    try:
        return fn()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ReviewConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TokenCountError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def frontend() -> HTMLResponse:
    html = _FRONTEND_HTML.read_text(encoding="utf-8")
    return HTMLResponse(html.replace(_API_BASE_TOKEN, _api_base_url()))


@app.get("/reviews", response_model=list[ReviewRow])
def list_review_files() -> list[ReviewRow]:
    """Every file in the review table, decided or not."""
    return _call(
        lambda: [ReviewRow.model_validate(row) for row in service.list_files()]
    )


@app.get("/reviews/{project}/{filename}", response_model=ReviewDetail)
def open_review(project: str, filename: str, engine: str) -> ReviewDetail:
    """Markdown and layout for a file whose review decision is still empty."""
    return _call(
        lambda: ReviewDetail.model_validate(
            service.open_review(project, filename, engine)
        )
    )


@app.get("/reviews/{project}/{filename}/layout")
def review_layout(project: str, filename: str, engine: str) -> FileResponse:
    """Layout PDF for comparing detected regions with the extracted text."""
    path = _call(lambda: service.layout_file(project, filename, engine))
    return FileResponse(path, media_type="application/pdf")


@app.get("/reviews/{project}/{filename}/assets/{asset_path:path}")
def review_asset(
    project: str, filename: str, engine: str, asset_path: str
) -> FileResponse:
    """Serve an image referenced by the markdown under review."""
    path = _call(lambda: service.asset_file(project, filename, engine, asset_path))
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)


@app.post("/reviews/{project}/{filename}", response_model=ReviewRow)
def submit_review(
    project: str, filename: str, engine: str, body: ReviewDecision
) -> ReviewRow:
    """Update token_num, approved, and require_chunking after a review."""
    saved = _call(
        lambda: service.submit_review(
            project,
            filename,
            engine,
            approved=body.approved,
            text=body.text,
            require_chunking=body.require_chunking,
            create_time=body.create_time,
        )
    )
    return ReviewRow.model_validate(saved)
