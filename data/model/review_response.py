from pydantic import BaseModel, Field

from data.model.final_answer import FinalAnswer


class ReviewResponse(BaseModel):
    approved: bool = Field(default=True, description="approved or rejected")
    require_chunking: bool = Field(
        default=False,
        description="Whether the human wants the approved text to be chunked.",
    )
    content: str | None = Field(default=None, description="The content approved.")
    final_answer: FinalAnswer | None = Field(
        default=None,
        description="The final answer approved.",
    )
