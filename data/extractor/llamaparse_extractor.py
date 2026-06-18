from llama_cloud import AsyncLlamaCloud
from pathlib import Path
import logging
import os

logger = logging.getLogger(__name__)


class LlamaParseExtractor:
    """
    Extract text from a file using LlamaParse API.
    Files have to be uploaded to LlamaCloud first, which should be a compliance problem.
    """

    def __init__(self):
        self.api_key = os.environ.get("LLAMA_CLOUD_API_KEY")

    async def extract(self, source: Path) -> str:
        logger.info(f"Extracting text with LlamaParse for file: {source}")
        client = AsyncLlamaCloud(api_key=self.api_key)
        file_id = (await client.files.create(file=source, purpose="parse")).id

        result = await client.parsing.parse(
            file_id=file_id,
            tier="agentic",
            version="latest",
            expand=["markdown_full"],
        )

        await client.files.delete(file_id=file_id)

        return result.markdown_full
