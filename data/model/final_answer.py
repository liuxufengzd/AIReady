from pydantic import BaseModel, Field


class FinalAnswer(BaseModel):
    texts_for_keyword_search: list[str] | None = Field(
        default=None, description="The texts for keyword search"
    )
    texts_for_semantic_search: list[str] | None = Field(
        default=None, description="The texts for semantic search"
    )
    meta_extension: BaseModel | None = Field(
        default=None, description="The extension of the file"
    )
