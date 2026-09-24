from pydantic import BaseModel, Field


class ImageMeta(BaseModel):
    content: str = Field(
        description="The content of the image, which should contain all crucial information of the image"
    )
    info_loss: bool = Field(
        default=False,
        description="Whether the image content is lossy. If True, the original image should be remained",
    )
