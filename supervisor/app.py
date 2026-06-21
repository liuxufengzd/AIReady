import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from supervisor.model.thread import Thread, ThreadSummary
from supervisor.supervisor_service import SupervisorService

load_dotenv(Path(__file__).parent / ".env")

_FILES_BASE_URL = os.getenv("FILES_BASE_URL", "http://localhost:8002/files")
service = SupervisorService(files_base_url=_FILES_BASE_URL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Everything before yield is startup logic, everything after is shutdown logic.
    FastAPI guarantees the code after yield runs on graceful shutdown (Ctrl+C, SIGTERM, etc.)."""
    yield
    await service.close()


app = FastAPI(
    title="Supervisor API",
    description=(
        "AI-powered document Q&A with query decomposition, synthesis, "
        "and persistent multi-turn conversation threads."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_FILES_DIR = Path(os.getenv("FILES_STORE", r"D:\Workspace\AIReady\store\s3\processed"))
_FILES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/files", StaticFiles(directory=str(_FILES_DIR)), name="files")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class QueryRequest(BaseModel):
    project: str
    query: str
    thread_id: str | None = None
    filters: dict[str, str] | None = None


class QueryResponse(BaseModel):
    answer: str
    thread_id: str
    title: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.post("/query", response_model=QueryResponse)
async def query(request: QueryRequest) -> QueryResponse:
    """
    Answer a user question within an optional conversation thread.

    - ``project``   - project identifier used to scope knowledge searches
    - ``query``     - the user's natural-language question
    - ``thread_id`` - (optional) existing thread ID; omit to start a new thread
    - ``filters``   - optional key/value metadata filters for knowledge search

    The response includes ``thread_id`` so the client can continue the same
    conversation in subsequent requests, and ``title`` (populated on the first
    turn of a new thread).
    """
    try:
        result = await service.query(
            project=request.project,
            query=request.query,
            thread_id=request.thread_id,
            filters=request.filters,
        )
        return QueryResponse(
            answer=result.answer,
            thread_id=result.thread_id,
            title=result.title,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/threads", response_model=list[ThreadSummary])
async def list_threads(project: str, limit: int = 100) -> list[ThreadSummary]:
    """
    List the most recently updated conversation threads for a project.

    - ``project`` - project identifier
    - ``limit``   - maximum number of threads to return (default 100)
    """
    try:
        return await service.list_threads(project, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/threads/{thread_id}", response_model=Thread)
async def get_thread(thread_id: str) -> Thread:
    """
    Retrieve the complete conversation history for a thread.
    All Q&A pairs are returned in chronological order.
    """
    try:
        thread = await service.get_thread(thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail="Thread not found")
        return thread
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.delete("/threads/{thread_id}", status_code=204)
async def delete_thread(thread_id: str) -> None:
    """
    Delete a conversation thread and all associated historical topic summaries
    from the vector database.
    """
    try:
        await service.delete_thread(thread_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
