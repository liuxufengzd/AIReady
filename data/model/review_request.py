from pydantic import BaseModel, Field


class ReviewRequest(BaseModel):
    session_id: str | None = Field(
        default=None, description="The session id of the extraction operation."
    )
    content: str | None = Field(default=None, description="The content to review.")
    ask_chunking: bool = Field(
        default=True, description="Whether to ask the human to chunk the text."
    )
    ask_extension: bool = Field(
        default=False,
        description="Whether to ask the human to input the metadata extension.",
    )
    permit_reject: bool = Field(
        default=True, description="Whether to permit the human to reject the text."
    )
    token_num: int | None = Field(
        default=None,
        description="Token count of the extracted text, shown to help the human decide on chunking.",
    )
    extension: BaseModel | None = Field(
        default=None,
        description="The extension of the file, extracted by the LLM from the file content.",
    )
