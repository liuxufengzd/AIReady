from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from typing_extensions import override

from core.common import const
from core.common.logger import get_logger
from core.common.prompts import PDF_SUMMARIZE
from core.common.utils import read_pdf
from core.extractors.extractor import Extractor

logger = get_logger(__name__)


class PDFExtractor(Extractor):
    @override
    async def summarize_with_vlm(self, source: Path, context: str = "") -> str:
        logger.info(f"Extracting PDF: {source}")
        message = HumanMessage(
            content=[
                {"type": "text", "text": PDF_SUMMARIZE.format(context=context)},
                read_pdf(source),
            ]
        )
        result = await self.llm.ainvoke([message])
        res = result.text
        output_tokens = self.llm.get_num_tokens(res)

        # If token count is too high, ask for a more concise summary.
        if output_tokens > const.EMBEDDING_TOKEN_LIMIT:
            logger.info(
                "Output tokens exceeds the limit, asking the LLM to summarize the PDF in a more concise way."
            )
            error_message = f"The output tokens {output_tokens} exceeds the limit {const.EMBEDDING_TOKEN_LIMIT}. Please summarize the PDF in a more concise way."
            result = await self.llm.ainvoke(
                [message, SystemMessage(content=error_message)]
            )
            output_tokens = self.llm.get_num_tokens(result.text)
            if output_tokens > const.EMBEDDING_TOKEN_LIMIT:
                logger.warning(
                    f"The output tokens {output_tokens} exceeds the limit {const.EMBEDDING_TOKEN_LIMIT} after the second attempt. Truncating the result to the limit."
                )
                res = result.text[: const.EMBEDDING_TOKEN_LIMIT]

        logger.info(f"Result token count: {output_tokens}")
        return res
