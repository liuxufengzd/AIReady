from langchain_google_genai import ChatGoogleGenerativeAI
from common import const
from pathlib import Path
import base64
import mimetypes
import os


def get_llm(more_thinking: bool = False, temperature: float = 1.0):
    return ChatGoogleGenerativeAI(
        model=const.LLM_NAME if not more_thinking else const.SOTA_LLM_NAME,
        temperature=temperature,
        max_retries=3,
    )


def read_file(source: Path) -> dict[str, str]:
    if source.suffix == ".pdf":
        return _read_pdf(source)
    elif source.suffix in [".jpg", ".jpeg", ".png"]:
        return _read_image(source)
    else:
        raise ValueError(f"Unsupported file type: {source.suffix}")


def _read_image(source: Path) -> dict[str, str]:
    mime_type = mimetypes.guess_type(source)[0]
    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:{mime_type};base64,{base64.b64encode(source.read_bytes()).decode('utf-8')}"
        },
    }


def _read_pdf(source: Path) -> dict[str, str]:
    return {
        "type": "file",
        "source_type": "base64",
        "mime_type": "application/pdf",
        "data": base64.b64encode(source.read_bytes()).decode("utf-8"),
    }


def get_db_uri():
    return f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
