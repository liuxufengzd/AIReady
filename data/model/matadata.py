from pydantic import BaseModel, Field, SerializeAsAny


class Chunk(BaseModel):
    id: str = Field(description="The ID of the chunk")
    page_num: int | None = Field(
        default=None, description="The page number of the chunk, used for pptx files"
    )
    semantic_text: str | None = Field(
        default=None,
        description="The semantic text of the chunk, used for semantic search",
    )
    keyword_text: str | None = Field(
        default=None,
        description="The keyword text of the chunk, used for keyword search",
    )

    retrieve_raw_file: bool = Field(
        default=False,
        description="Whether to retrieve the raw file for the chunk",
    )


# Metadata definition
class Metadata(BaseModel):
    project: str | None = Field(
        default=None, description="The project name of the file"
    )
    mime_type: str | None = Field(default=None, description="The MIME type of the file")
    size: int | None = Field(default=None, description="The size of the file")
    file_name: str | None = Field(default=None, description="The name of the file")
    # Stored as JSONB in the database
    chunks: list[Chunk] = Field(
        default_factory=list, description="The chunks of the file"
    )
    # File level, used to minimize the scope of the chunks to search. Extracted by the LLM from the file content.
    extension: SerializeAsAny[BaseModel] | None = Field(
        default=None, description="The extension of the file"
    )


# Metadata extensions
class IHIMachineReport(BaseModel):
    customer_name: str = Field(default="unknown", description="お客さま名")
    machine_group: str = Field(default="unknown", description="機種")
    machine_type: str = Field(default="unknown", description="型式")
    manufacturing_factory: str = Field(default="unknown", description="製造工場")
    non_conforming_work: str = Field(default="unknown", description="不適合発生工事")
    non_conforming_work_name: str = Field(
        default="unknown", description="不適合工事名称"
    )
    work_classification: str = Field(default="unknown", description="工事種類")
    date: str = Field(default="unknown", description="発生日")
