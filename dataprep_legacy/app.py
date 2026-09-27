import os
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from data.executor import Executor
from data.model.review_request import ReviewRequest
from data.model.review_response import ContentReviewBody
from data.common.utils import parse_extension

load_dotenv(Path(__file__).parent / ".env")

executor = Executor()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await executor.close()


app = FastAPI(
    title="DataExtractor API",
    description="API for data extraction from a file",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_FRONTEND_HTML = Path(__file__).resolve().parent.parent / "frontend" / "data" / "index.html"
_API_BASE_TOKEN = "__API_BASE_URL__"


def _api_base_url() -> str:
    configured = os.getenv("API_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    host = os.getenv("HOST", "localhost").strip() or "localhost"
    if host in {"0.0.0.0", "::", "[::]"}:
        host = "localhost"
    port = os.getenv("PORT", "8001").strip() or "8001"
    return f"http://{host}:{port}"


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def frontend() -> HTMLResponse:
    html = _FRONTEND_HTML.read_text(encoding="utf-8")
    return HTMLResponse(html.replace(_API_BASE_TOKEN, _api_base_url()))

# =============================================================================
# HITL extraction flow
#
#  Step 1 — POST /start_extraction
#            Starts the workflow.  Returns a ReviewRequest with:
#              • session_id   - must be echoed in every subsequent call
#              • content     – markdown read from the review directory for this response
#              • ask_chunking - whether to ask the human to chunk the text
#              • ask_extension - whether to ask the human to input the metadata extension
#              • permit_reject - whether to permit the human to reject the text
#              • token_num   – token count to inform the chunking decision
#              • layout      – MinerU layout PDF bytes (base64 in JSON), when
#                              one was drawn, for comparing detected regions
#                              with the parsed text
#
#  Step 2 — POST /continue_extraction
#            Query: session_id.
#            JSON body: approval, the full revised markdown, chunking flag,
#            and an optional metadata extension. The markdown must stay in the
#            body; a query parameter is truncated once the document is large.
#
#  Step 3 — POST /post_extraction
#            Human submits the LLM extracted metadata extension.
#            Return human reviewed/revised metadata extension.
# =============================================================================


@app.post("/start_extraction", response_model=ReviewRequest)
async def start_extraction(
    project: str,
    source: str,
    extension: str | None = None,
) -> ReviewRequest:
    """Begin extraction. Returns the text content to review."""
    try:
        return await executor.start(
            project,
            Path(source).expanduser().resolve(),
            meta_schema=parse_extension(extension) if extension else None,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/continue_extraction", response_model=ReviewRequest | None)
async def continue_extraction(
    session_id: str,
    body: ContentReviewBody,
) -> ReviewRequest | None:
    """Resume the graph after the extracted content is reviewed by the human"""
    try:
        return await executor.continue_after_content_review(
            session_id,
            approved=bool(body.approved),
            text=body.text,
            require_chunking=bool(body.require_chunking),
            extension=body.extension,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/post_extraction")
async def post_extraction(
    session_id: str,
    extension: dict | None = None,
) -> ReviewRequest | None:
    """Resume the graph with the human-reviewed extension(LLM extracted)"""
    try:
        return await executor.continue_after_extension_review(session_id, extension)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
