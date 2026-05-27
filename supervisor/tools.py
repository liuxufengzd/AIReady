import os
from langchain.tools import ToolRuntime, tool
from supervisor.search_context import SearchContext
from grpc_protos.search.search_client import SearchClient
from data.model.matadata import Metadata
from pathlib import Path
from common.logger import get_logger
from common.util import read_file
from langchain_core.messages import ToolMessage
from langgraph.types import Command

logger = get_logger(__name__)


@tool
async def search_domain_knowledge(
    query: str, runtime: ToolRuntime[SearchContext]
) -> Command:
    """Searches for documents in the domain knowledge base that are related to the given query.
    Returns a list of content blocks containing the related domain knowledge, or an error message.

    Args:
        query: The query to search for documents.
    """
    try:
        logger.info(f"Searching domain knowledge for query: {query}")
        context = runtime.context

        async with SearchClient(
            project=context.project,
            target=os.environ.get("SEARCH_API_URL"),
        ) as client:
            file_name_to_chunk_ids = await client.query(query, filters=context.filters)

        if not file_name_to_chunk_ids:
            return "No relevant documents found for the query."

        content_blocks: list[dict[str, str]] = []
        for file_name, chunk_ids in file_name_to_chunk_ids.items():
            for chunk_id in chunk_ids:
                try:
                    metadata: Metadata = _get_metadata(context.project, file_name)
                except Exception as e:
                    logger.warning(f"Error getting metadata for file {file_name}: {e}")
                    continue

                # For production, search the chunk in postgres database
                chunk = next((c for c in metadata.chunks if c.id == chunk_id), None)
                if not chunk:
                    logger.warning(f"Chunk {chunk_id} not found in file {file_name}")
                    continue
                if chunk.retrieve_raw_file:
                    file_path = Path(file_name)
                    if file_path.suffix == ".pptx":
                        path = Path(
                            f"store/s3/processed/{context.project}/{file_path.stem}/slide_{chunk.page_num}.png"
                        )
                    else:
                        path = Path(f"store/s3/processed/{context.project}/{file_name}")
                    if not path.exists():
                        logger.warning(f"File {path} not found")
                        continue
                    boundary_start = {
                        "type": "text",
                        "text": (
                            f"\n====== Start of Multimodal File ======\n"
                            f"[File Name]: {file_name}\n"
                            f"[File Content]:\n"
                        ),
                    }
                    boundary_end = {
                        "type": "text",
                        "text": "\n====== End of Multimodal File ======\n",
                    }
                    content_blocks.extend(
                        [boundary_start, read_file(path), boundary_end]
                    )
                else:
                    boundary_start = {
                        "type": "text",
                        "text": (
                            f"\n====== Start of Text Chunk ======\n"
                            f"[File Name]: {file_name}\n"
                            f"[Chunk Content]:\n"
                        ),
                    }
                    boundary_end = {
                        "type": "text",
                        "text": "\n====== End of Text Chunk ======\n",
                    }
                    content_blocks.extend(
                        [
                            boundary_start,
                            {"type": "text", "text": chunk.keyword_text},
                            boundary_end,
                        ]
                    )

        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=content_blocks, tool_call_id=runtime.tool_call_id
                    )
                ],
            }
        )
    except Exception as e:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=f"Error searching domain documents: {str(e)}",
                        tool_call_id=runtime.tool_call_id,
                    )
                ],
            }
        )


def _get_metadata(project: str, file_name: str) -> Metadata:
    with open(
        f"store/postgres/{project}/{Path(file_name).stem}.json", "r", encoding="utf-8"
    ) as f:
        return Metadata.model_validate_json(f.read())
