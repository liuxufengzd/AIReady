from typing_extensions import override
from core.extractors.extractor import Extractor
from pathlib import Path
from core.common.utils import read_image
from langchain_core.messages import HumanMessage, SystemMessage
from core.common.prompts import IMAGE_SUMMARIZE

from core.common.logger import get_logger
from core.common import const

logger = get_logger(__name__)


class ImageExtractor(Extractor):
    @override
    async def summarize_with_vlm(self, source: Path, context: str = "") -> str:
        logger.info(f"Extracting image: {source}")
        message = HumanMessage(
            content=[
                {"type": "text", "text": IMAGE_SUMMARIZE.format(context=context)},
                read_image(source),
            ]
        )
        result = await self.llm.ainvoke([message])
        res = result.text
        output_tokens = self.llm.get_num_tokens(res)

        # if the output tokens exceeds the limit, ask the LLM to summarize the image in a more concise way
        if output_tokens > const.EMBEDDING_TOKEN_LIMIT:
            logger.info(
                "Output tokens exceeds the limit, asking the LLM to summarize the image in a more concise way."
            )
            error_message = f"The output tokens {output_tokens} exceeds the limit {const.EMBEDDING_TOKEN_LIMIT}. Please summarize the image in a more concise way."
            result = await self.llm.ainvoke(
                [message, SystemMessage(content=error_message)]
            )
            output_tokens = self.llm.get_num_tokens(result.text)
            if output_tokens > const.EMBEDDING_TOKEN_LIMIT:
                logger.warning(
                    f"The output tokens {output_tokens} exceeds the limit {const.EMBEDDING_TOKEN_LIMIT} after the second attempt. Truncating the result to the limit."
                )
                # truncate the result to the limit
                res = result.text[: const.EMBEDDING_TOKEN_LIMIT]

        logger.info(f"Result token count: {output_tokens}")
        return res
