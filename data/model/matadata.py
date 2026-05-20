from pydantic import BaseModel, Field, SerializeAsAny


# Page information definition
class Page(BaseModel):
    page_num: int = Field(description="The page number of the page")
    semantic_text: str = Field(
        description="Summary of the page, used for semantic search"
    )
    keyword_text: str = Field(
        description="Keywords of the page, used for keyword search"
    )


# Metadata definition
class Metadata(BaseModel):
    project: str | None = Field(
        default=None, description="The project name of the file"
    )
    mime_type: str | None = Field(default=None, description="The MIME type of the file")
    size: int | None = Field(default=None, description="The size of the file")
    file_name: str | None = Field(default=None, description="The name of the file")
    extension: SerializeAsAny[BaseModel] | None = Field(
        default=None, description="The extension of the file"
    )
    semantic_texts: list[str] | None = Field(
        default=None, description="Summaries of the file, used for semantic search"
    )
    keyword_texts: list[str] | None = Field(
        default=None, description="Keywords of the file, used for keyword search"
    )
    pages: list[Page] | None = Field(
        default=None, description="A list of pages in the file"
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
