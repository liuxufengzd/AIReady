import os
from langchain.tools import ToolRuntime, tool
from langchain_community.utilities import GoogleSerperAPIWrapper
from supervisor.search_context import SearchContext
from grpc_protos.search.search_client import SearchClient
import asyncio
from data.model.matadata import Metadata
from pathlib import Path
from common.logger import get_logger
from common.util import read_file

logger = get_logger(__name__)


@tool
async def search_domain_knowledge(
    query: str, runtime: ToolRuntime[SearchContext]
) -> list[dict[str, str]] | str:
    """Searches for documents in the domain knowledge base that are related to the given query.
    Returns a list of content blocks containing the related domain knowledge, or an error message.

    Args:
        query: The query to search for documents.
    """
    try:
        context = runtime.context

        async with SearchClient(
            project=context.project,
            target=os.environ.get("SEARCH_API_URL"),
        ) as client:
            file_name_to_chunk_ids = await client.query(query, filters=context.filters)

        if not file_name_to_chunk_ids:
            return "No relevant documents found for the query."

        content_blocks: list[dict[str, str]] = []
        texts: list[str] = []
        for file_name, chunk_ids in file_name_to_chunk_ids.items():
            file_path = Path(file_name)
            for chunk_id in chunk_ids:
                metadata: Metadata = _get_metadata(context.project, file_name)

                # For production, search the chunk in postgres database
                chunk = next((c for c in metadata.chunks if c.id == chunk_id), None)
                if not chunk:
                    logger.warning(f"Chunk {chunk_id} not found in file {file_name}")
                    continue
                if chunk.retrieve_raw_file:
                    if not file_path.exists():
                        logger.warning(f"File {file_name} not found")
                        continue
                    if file_path.suffix == ".pptx":
                        path = Path(
                            f"store/s3/processed/{context.project}/{file_path.stem}/slide_{chunk.page_num}.png"
                        )
                    else:
                        path = Path(f"store/s3/processed/{context.project}/{file_name}")
                    content_blocks.append(read_file(path))
                else:
                    texts.append(chunk.keyword_text)

        if texts:
            content_blocks.append({"type": "text", "text": "\n---\n".join(texts)})

        return content_blocks
    except Exception as e:
        return f"Error searching domain documents: {e}"


@tool
async def web_search(query: str) -> str:
    """Searches the web for information related to the given query.
    Returns a string containing the search results or an error message.

    Args:
        query: The query to search the web for.
    """
    try:
        # Run the synchronous API call in an executor
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: GoogleSerperAPIWrapper().run(query),
        )
    except Exception as e:
        return f"Error searching the web: {e}"


def _get_metadata(project: str, file_name: str) -> Metadata:
    with open(
        f"store/postgres/{project}/{Path(file_name).stem}.json", "r", encoding="utf-8"
    ) as f:
        return Metadata.model_validate_json(f.read())
