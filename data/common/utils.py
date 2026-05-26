from data.common import const
from pathlib import Path
from data.model.matadata import Metadata
from langchain_text_splitters import MarkdownTextSplitter
from pydantic import BaseModel, Field, create_model
from typing import Type
import json
import uuid


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


def parse_extension(extension: str) -> Type[BaseModel]:
    """The extension should be like:
    [
        {"name":"field name","type":"str","default":"UNKNOWN","description":"field description"},
        ...
    ]
    """
    fields_config = json.loads(extension)
    json.loads(extension)
    fields = {}

    for item in fields_config:
        field_name = item["name"]
        field_type = const.TYPE_MAPPING.get(item.get("type", "str"), str)
        field_default = item.get("default", None)
        field_description = item["description"]

        fields[field_name] = (
            field_type,
            Field(default=field_default, description=field_description),
        )
    temp_model_name = f"DynamicModel_{uuid.uuid4().hex[:8]}"
    return create_model(temp_model_name, **fields)
