from pathlib import Path
from kreuzberg import (
    extract_file,
    ExtractionConfig,
    OcrConfig,
)
from data.common.utils import get_llm
from data.common.prompts import PROMPT_FOR_KEYWORD, PROMPT_FOR_SEMANTIC
from langchain_core.messages import HumanMessage, AIMessage
from data.common.utils import read_pdf, read_image

from data.common.logger import get_logger
from data.common import const

logger = get_logger(__name__)


class Extractor:
    def __init__(self):
        self.llm = get_llm()

    async def extract_with_ocr(
        self,
        source: Path,
        languages: list[str],
    ) -> str:
        logger.info(f"Extracting OCR text with PaddleOCR for file: {source}")
        config: ExtractionConfig = ExtractionConfig(
            use_cache=True,
            ocr=OcrConfig(
                backend="paddleocr",
                language="+".join(languages),
                paddle_ocr_config={"model_tier": "server"},
            ),
        )
        text = await extract_file(source, config=config)
        return text.content

    async def extract_keyword_with_llm(self, source: Path) -> str:
        logger.info(f"Extracting keyword text with LLM for file: {source}")
        if source.suffix == ".pdf":
            file_content = read_pdf(source)
        else:
            file_content = read_image(source)
        message = HumanMessage(
            content=[
                {
                    "type": "text",
                    "text": PROMPT_FOR_KEYWORD,
                },
                file_content,
            ]
        )
        result = await self.llm.ainvoke([message])
        return result.text

    async def extract_summary_with_llm(self, source: Path, context: str = "") -> str:
        logger.info(f"Extracting summary with LLM for file: {source}")
        if source.suffix == ".pdf":
            file_content = read_pdf(source)
        else:
            file_content = read_image(source)
        message = HumanMessage(
            content=[
                {
                    "type": "text",
                    "text": PROMPT_FOR_SEMANTIC.format(context=context),
                },
                file_content,
            ]
        )
        result = await self.llm.ainvoke([message])

        # if the output tokens exceeds the limit, ask the LLM to summarize the file in a more concise way
        res = result.text
        token_number = self.llm.get_num_tokens(res)
        if token_number > const.EMBEDDING_TOKEN_LIMIT:
            logger.info(
                "Output tokens exceeds the limit, asking the LLM to summarize the file in a more concise way."
            )
            error_message = f"The output tokens {token_number} exceeds the limit {const.EMBEDDING_TOKEN_LIMIT}. Summarize the file in a more CONCISE way."
            result = await self.llm.ainvoke(
                [
                    message,
                    AIMessage(content=res),
                    HumanMessage(content=error_message),
                ]
            )
            res = result.text
            token_number = self.llm.get_num_tokens(res)
            if token_number > const.EMBEDDING_TOKEN_LIMIT:
                logger.warning(
                    f"The output tokens {token_number} exceeds the limit {const.EMBEDDING_TOKEN_LIMIT} after the second attempt. Truncating the result to the limit."
                )
                # truncate the result to the limit
                res = res[: const.EMBEDDING_TOKEN_LIMIT]

        logger.info(f"Result token count: {token_number}")
        return res
