from pathlib import Path
from common.util import get_llm
from data.common.prompts import PROMPT_FOR_KEYWORD, PROMPT_FOR_SEMANTIC
from langchain_core.messages import HumanMessage, AIMessage
from common.util import read_file

from common.logger import get_logger
from data.common import const

logger = get_logger(__name__)


class VLMExtractor:
    def __init__(self):
        self.llm = get_llm()

    async def extract_keyword(self, source: Path) -> str:
        logger.info(f"Extracting keyword text with VLM for file: {source}")
        file_content = read_file(source)
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

    async def extract_summary(
        self, source: Path = None, text: str = None, context: str = ""
    ) -> str:
        logger.info("Extracting summary with VLM")
        if source is not None:
            file_content = read_file(source)
        elif text is not None:
            file_content = text
        else:
            raise ValueError("Either source or text must be provided")
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
