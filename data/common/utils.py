from langchain_google_genai import ChatGoogleGenerativeAI
from data.common import const
from pathlib import Path
import base64
import mimetypes
from data.model.matadata import Metadata
from langchain_text_splitters import MarkdownTextSplitter


def get_llm(more_thinking: bool = False, temperature: float = 1.0):
    return ChatGoogleGenerativeAI(
        model=const.LLM_NAME if not more_thinking else const.SOTA_LLM_NAME,
        temperature=temperature,
        max_retries=3,
    )


def read_file(source: Path) -> dict:
    if source.suffix == ".pdf":
        return _read_pdf(source)
    elif source.suffix in [".jpg", ".jpeg", ".png"]:
        return _read_image(source)
    else:
        raise ValueError(f"Unsupported file type: {source.suffix}")


def _read_image(source: Path) -> dict:
    mime_type = mimetypes.guess_type(source)[0]
    return {
        "type": "image_url",
        "image_url": f"data:{mime_type};base64,{base64.b64encode(source.read_bytes()).decode('utf-8')}",
    }


def _read_pdf(source: Path) -> dict:
    return {
        "type": "media",
        "mime_type": "application/pdf",
        "data": base64.b64encode(source.read_bytes()).decode("utf-8"),
    }


def store_metadata(project: str, file_name: str, metadata: Metadata) -> None:
    if not Path(f"store/nosql/{project}").exists():
        Path(f"store/nosql/{project}").mkdir(parents=True, exist_ok=True)
    metadata_path = Path(f"store/nosql/{project}/{Path(file_name).stem}.json")
    with open(metadata_path, "w", encoding="utf-8") as f:
        f.write(metadata.model_dump_json(indent=4, exclude_none=True))


def chunk_md(
    md_text: str,
    chunk_size: int = const.DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = const.DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    splitter = MarkdownTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    return splitter.split_text(md_text)
