from pydantic import BaseModel, Field
from datetime import datetime


class QAPair(BaseModel):
    query: str
    answer: str
    tokens: int


class Thread(BaseModel):
    thread_id: str
    project: str
    title: str | None = None
    body: list[QAPair] = Field(default_factory=list)
    # Number of QA pairs that have been compacted into the vector DB.
    extracted_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ThreadSummary(BaseModel):
    thread_id: str
    project: str
    title: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
