from pydantic import BaseModel, Field


class ReviewResponse(BaseModel):
    approved: bool = Field(default=True, description="approved or rejected")
    require_chunking: bool = Field(
        default=False,
        description="Whether the human wants the approved text to be chunked.",
    )
    content: str | None = Field(default=None, description="The content approved.")
    extension: dict | None = Field(
        default=None,
        description="The extension of the file, reviewed or input by the human.",
    )
