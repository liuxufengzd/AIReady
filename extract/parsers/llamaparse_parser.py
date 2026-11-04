from pathlib import Path
import os
from llama_cloud import AsyncLlamaCloud

from parsers.parser_base import ParserBase


class LlamaParseParser(ParserBase):
    def __init__(
        self,
        tier: str = "agentic",
    ):
        self._client = AsyncLlamaCloud(api_key=os.getenv("LLAMAPARSE_API_KEY"))
        self._tier = tier

    async def parse(self, file_path: Path) -> str:
        file_obj = await self._client.files.create(
            file=Path(file_path), purpose="parse"
        )
        result = await self._client.parsing.parse(
            file_id=file_obj.id,
            tier=self._tier,
            version="latest",
            expand=["markdown_full"],
        )
        return result.markdown_full or ""
