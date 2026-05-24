from pydantic import BaseModel, Field


class TextPair(BaseModel):
    semantic_text: str = Field(description="The semantic text of this chunk")
    keyword_text: str = Field(description="The keyword text of this chunk")
