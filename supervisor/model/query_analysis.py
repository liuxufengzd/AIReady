from pydantic import BaseModel, Field


class ContextAnswer(BaseModel):
    question: str = Field(
        description=(
            "A sub-question whose requested facts are explicitly stated in the conversation history."
        )
    )
    answer: str = Field(
        description=(
            "The facts stated in the conversation history, in the same language as the question. "
            "Do not use this field to say the record is missing, that external systems are inaccessible, "
            "or to tell the user to look the facts up elsewhere."
        )
    )


class QueryAnalysisResult(BaseModel):
    language: str = Field(description="The language of the original question")
    answered_from_context: list[ContextAnswer] | None = Field(
        default=None,
        description=(
            "Questions whose requested facts are explicitly present in conversation history. "
            "Leave empty when history does not state those facts."
        ),
    )
    retrieval_questions: list[str] | None = Field(
        default=None,
        description=(
            "Unresolved factual questions to search in the user's uploaded document knowledge base. "
            "Required when conversation history does not explicitly state the requested facts. "
            "Do not leave this empty just because history has no record or the facts might also exist on an external website."
        ),
    )
