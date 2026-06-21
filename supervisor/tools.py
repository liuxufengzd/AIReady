import os
from langchain.tools import ToolRuntime, tool
from supervisor.model.search_context import SearchContext
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
) -> Command | str:
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
        current_filename_to_chunk_ids: dict[str, list[str]] = runtime.state.get(
            "filename_to_chunk_ids", {}
        )
        for file_name, chunk_ids in file_name_to_chunk_ids.items():
            for chunk_id in chunk_ids:
                try:
                    # Avoid adding the same file chunk multiple times to the content blocks
                    if (
                        file_name in current_filename_to_chunk_ids
                        and chunk_id in current_filename_to_chunk_ids[file_name]
                    ):
                        continue
                    else:
                        current_filename_to_chunk_ids.setdefault(file_name, []).append(
                            chunk_id
                        )
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
                "filename_to_chunk_ids": current_filename_to_chunk_ids,
            }
        )
    except Exception as e:
        return f"Error searching domain documents: {str(e)}"


@tool
async def search_for_image(
    file_name: str, image_id: str, runtime: ToolRuntime[SearchContext]
) -> Command | str:
    """Searches for the original image content in the image store. Use this tool if and only if the original image content is required.
    Returns a content block containing the original image content, or an error message.

    Args:
        file_name: The name of the file containing the image.
        image_id: The ID of the image to search for, which is recorded in the <Image><ID>{image_id}</ID></Image> tag.
    """
    try:
        context = runtime.context
        logger.info(
            f"Searching for image with project: {context.project}, file name: {file_name}, image ID: {image_id}"
        )
        image_path = Path(
            f"store/s3/images/{context.project}/{file_name}/{image_id}.jpg"
        )
        if not image_path.exists():
            logger.warning(
                f"Image with ID {image_id} not found. File name: {file_name}, image ID: {image_id}"
            )
            return f"Image with ID {image_id} not found. Check the file name and image ID again."
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=[read_file(image_path)],
                        tool_call_id=runtime.tool_call_id,
                    )
                ],
            }
        )
    except Exception as e:
        return f"Error searching for image: {str(e)}"


@tool
async def search_conversation_history(
    query: str, runtime: ToolRuntime[SearchContext]
) -> str:
    """Search historical conversation summaries for information relevant to the query.
    Use this tool when the current question references topics from earlier conversation
    turns that are no longer present in the recent conversation history.  The tool returns the top-3
    semantically matching topic summaries extracted from the archived history.

    Args:
        query: Natural-language description of the historical information needed.
    """
    logger.info(f"Searching conversation history for query: {query}")
    context = runtime.context
    if not context.thread_id:
        return "No conversation history is available for this session."

    try:
        async with SearchClient(target=os.environ.get("SEARCH_API_URL")) as client:
            contents = await client.query_topics(
                thread_id=context.thread_id,
                query=query,
                top_k=3,
            )
    except Exception as exc:
        logger.warning("Failed to search conversation history: %s", exc)
        return "Failed to retrieve conversation history."

    if not contents:
        return "No relevant historical conversation information found."

    return "\n\n---\n\n".join(contents)


def _get_metadata(project: str, file_name: str) -> Metadata:
    with open(
        f"store/postgres/{project}/{Path(file_name).stem}.json", "r", encoding="utf-8"
    ) as f:
        return Metadata.model_validate_json(f.read())
