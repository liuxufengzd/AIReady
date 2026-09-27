from pydantic import BaseModel, Field


class ReviewResponse(BaseModel):
    approved: bool = Field(default=True, description="approved or rejected")
    require_chunking: bool = Field(
        default=False,
        description="Whether the human wants the approved text to be chunked.",
    )
    extension: dict | None = Field(
        default=None,
        description="The extension of the file, reviewed or input by the human.",
    )


class ContentReviewBody(BaseModel):
    """Body of POST /continue_extraction.

    The approved markdown travels here. A query parameter is percent-encoded
    into the request URL and gets cut off once the document is large.
    """

    approved: bool | None = None
    text: str | None = None
    require_chunking: bool | None = None
    extension: dict | None = None
