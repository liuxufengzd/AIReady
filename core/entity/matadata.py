from pydantic import BaseModel, Field, SerializeAsAny


# Base Metadata
class Metadata(BaseModel):
    project: str = Field(description="The project name of the file")
    mime_type: str = Field(description="The MIME type of the file")
    size: int = Field(description="The size of the file")
    file_name: str = Field(description="The name of the file")
    extension: SerializeAsAny[BaseModel] | None = Field(
        default=None, description="The extension of the file"
    )


# Image
class ImageMetadata(Metadata):
    semantic_text: str = Field(
        description="Text of the image, used for semantic search"
    )
    keyword_text: str = Field(description="Text of the image, used for keyword search")


# PDF
class PDFMetadata(Metadata):
    semantic_text: str = Field(description="Text of the PDF, used for semantic search")
    keyword_text: str = Field(description="Text of the PDF, used for keyword search")


# PPTX
class PptxPage(BaseModel):
    page_num: int = Field(description="The page number of the PPTX file")
    semantic_text: str = Field(description="Text of the page, used for semantic search")
    keyword_text: str = Field(description="Text of the page, used for keyword search")


class PptxMetadata(Metadata):
    pages: list[PptxPage] = Field(
        default_factory=list, description="A list of pages in the PPTX file"
    )


# Metadata extensions
class IHIMachineReport(BaseModel):
    customer_name: str = Field(default=None, description="お客さま名")
    machine_group: str = Field(default=None, description="機種")
    machine_type: str = Field(default=None, description="型式")
    manufacturing_factory: str = Field(default=None, description="製造工場")
    non_conforming_work: str = Field(default=None, description="不適合発生工事")
    non_conforming_work_name: str = Field(default=None, description="不適合工事名称")
    work_classification: str = Field(default=None, description="工事種類")
    date: str = Field(default=None, description="発生日")
