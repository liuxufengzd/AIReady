import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from data.common import const
from data.executor import Executor
from data.model.final_answer import FinalAnswer
from data.model.review_request import ReviewRequest
from dotenv import load_dotenv

load_dotenv()

api = FastAPI(
    title="DataExtractor API",
    description="API for data extraction from documents",
)

api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

executor = Executor()

# =============================================================================
# HITL extraction flow
#
#  Step 1 — POST /start_extraction
#            Starts the workflow.  Returns a ReviewRequest with:
#              • session_id   - must be echoed in every subsequent call
#              • content     – raw extracted text
#              • ask_chunking - whether to ask the human to chunk the text
#              • token_num   – token count to inform the chunking decision
#
#  Step 2 — POST /continue_extraction
#            Human submits: approval/rejection, (optional) revised text,
#            and whether to chunk.  Returns a ReviewRequest with final_answer
#            populated so the human can confirm / edit before upload.
#
#  Step 3 — POST /post_extraction
#            Human submits the (optionally edited) final answer.
#            Returns null (non-PPT, or last PPT slide) or the next slide's
#            ReviewRequest (PPT mid-deck) so the frontend can loop through
#            all slides without extra orchestration.
# =============================================================================


@api.post("/start_extraction", response_model=ReviewRequest)
async def start_extraction(
    project: str,
    source: str,
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
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@api.post("/continue_extraction", response_model=ReviewRequest)
async def continue_extraction(
    session_id: str,
    approved: bool,
    text: str | None = None,
    require_chunking: bool = False,
) -> ReviewRequest:
    """
    Submit the text review decision.

    - ``approved``         - accept (True) or reject (False) the extracted text
    - ``text``             - revised text (required when approved=True)
    - ``require_chunking`` - split the text into chunks before indexing

    Returns a ReviewRequest whose ``final_answer`` contains the fully processed
    texts and metadata for the human to confirm before upload.
    """
    try:
        return await executor.continue_text_review(
            session_id,
            approved=approved,
            text=text,
            require_chunking=require_chunking,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.post("/post_extraction")
async def post_extraction(
    session_id: str,
    final_answer: FinalAnswer,
) -> ReviewRequest | None:
    """
    Submit the final-answer review.

    The human may have edited ``texts_for_keyword_search``,
    ``texts_for_semantic_search``, or ``meta_extension`` before submitting.

    Returns:
    - **null**          - extraction complete; metadata and raw file persisted.
    - **ReviewRequest** - (PPT only) the next slide's content to review.
    """
    try:
        return await executor.submit_final_answer(session_id, final_answer)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
