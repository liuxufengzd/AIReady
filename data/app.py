import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from data.common import const
from data.executor import Executor
from data.model.review_request import ReviewRequest
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

# =============================================================================
# HITL extraction flow
#
#  Step 1 — POST /start_extraction
#            Starts the workflow.  Returns a ReviewRequest with:
#              • session_id   - must be echoed in every subsequent call
#              • content     – raw extracted text
#              • ask_chunking - whether to ask the human to chunk the text
#              • ask_extension - whether to ask the human to input the metadata extension
#              • permit_reject - whether to permit the human to reject the text
#              • token_num   – token count to inform the chunking decision
#
#  Step 2 — POST /continue_extraction
#            Human submits: (optional) approval/rejection, (optional) revised text, (optional) metadata extension(User input),
#            and whether to chunk. Return user review result.
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
    languages: list[str] = const.DEFAULT_LANGUAGES,
) -> ReviewRequest:
    """Begin extraction. Returns the first ReviewRequest (text content to review)."""
    session_id = str(uuid.uuid4())
    try:
        return await executor.start(
            session_id,
            project,
            Path(source).expanduser().resolve(),
            languages=languages,
            meta_schema=parse_extension(extension) if extension else None,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/continue_extraction", response_model=ReviewRequest | None)
async def continue_extraction(
    session_id: str,
    approved: bool | None = None,
    text: str | None = None,
    require_chunking: bool | None = None,
) -> ReviewRequest | None:
    """Resume the graph after the extracted content is reviewed by the human"""
    try:
        return await executor.continue_after_content_review(
            session_id,
            approved=approved,
            text=text,
            require_chunking=require_chunking,
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
