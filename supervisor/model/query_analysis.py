from pydantic import BaseModel, Field


class QueryAnalysisResult(BaseModel):
    language: str = Field(description="The language of the original question")
    answered_from_context: list[tuple[str, str]] | None = Field(
        default=None,
        description="The list of questions that can be answered from the context. Each item is a tuple of two strings: the question and its answer",
    )
    retrieval_questions: list[str] | None = Field(
        default=None,
        description="The list of questions that require external knowledge retrieval",
    )
