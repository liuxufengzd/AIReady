from data.common import const
from pathlib import Path
from data.model.matadata import Metadata
from langchain_text_splitters import MarkdownTextSplitter


def store_metadata(project: str, metadata: Metadata) -> None:
    # Use Postgres for metadata storage. Extension can be stored as JSONB.
    if not Path(f"store/postgres/{project}").exists():
        Path(f"store/postgres/{project}").mkdir(parents=True, exist_ok=True)
    metadata_path = Path(
        f"store/postgres/{project}/{Path(metadata.file_name).stem}.json"
    )
    with open(metadata_path, "w", encoding="utf-8") as f:
        f.write(metadata.model_dump_json(indent=4, exclude_none=True))


def chunk_md(
    md_text: str,
    chunk_size: int = const.DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = const.DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    splitter = MarkdownTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    return splitter.split_text(md_text)
