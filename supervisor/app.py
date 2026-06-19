import os
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

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
    description="API for AI-powered document Q&A with query decomposition and synthesis",
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


class QueryRequest(BaseModel):
    project: str
    query: str
    filters: dict[str, str] | None = None


@app.post("/query")
async def query(request: QueryRequest) -> str:
    """
    Answer a user question

    - ``project`` - the project identifier used to scope knowledge searches
    - ``query``   - the user's natural-language question
    - ``filters`` - optional key/value metadata filters for the knowledge search
    """
    try:
        answer = await service.query(
            project=request.project,
            query=request.query,
            filters=request.filters,
        )
        return answer
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
