from pydantic import BaseModel, Field

from data.model.final_answer import FinalAnswer


class ReviewRequest(BaseModel):
    session_id: str | None = Field(
        default=None, description="The session id of the extraction operation."
    )
    content: str | None = Field(default=None, description="The content to review.")
    ask_chunking: bool = Field(
        default=True, description="Whether to ask the human to chunk the text."
    )
    token_num: int | None = Field(
        default=None,
        description="Token count of the extracted text, shown to help the human decide on chunking.",
    )
    final_answer: FinalAnswer | None = Field(
        default=None,
        description="The final answer to review.",
    )
