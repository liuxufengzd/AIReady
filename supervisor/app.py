from pathlib import Path
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from supervisor.supervisor_service import SupervisorService

load_dotenv(Path(__file__).parent / ".env")

app = FastAPI(
    title="Supervisor API",
    description="API for AI-powered document Q&A with query decomposition and synthesis",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

service = SupervisorService()


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
