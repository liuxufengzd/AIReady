from pydantic import BaseModel, Field


class TextPair(BaseModel):
    semantic_text: str = Field(description="The semantic text of this chunk")
    keyword_text: str = Field(description="The keyword text of this chunk")
    retrieve_raw_file: bool = Field(
        default=False,
        description="Whether to retrieve the raw file for the chunk",
    )
