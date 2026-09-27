from common.util import get_llm
from common.util import read_file
from data.common.prompts import IMAGE_META_PROMPT
from langchain_core.messages import HumanMessage, SystemMessage
from pathlib import Path
from data.model.matadata import ImageMeta
from common.logger import get_logger

logger = get_logger(__name__)


class ImageExtractor:
    def __init__(self):
        self.llm = get_llm()

    async def extract(self, source: Path) -> ImageMeta:
        logger.info(f"Extracting image metadata for file: {source}")
        file_content = read_file(source)

        image_meta: ImageMeta = await self.llm.with_structured_output(
            ImageMeta
        ).ainvoke(
            [
                SystemMessage(content=IMAGE_META_PROMPT),
                HumanMessage(content=[file_content]),
            ]
        )

        return image_meta
