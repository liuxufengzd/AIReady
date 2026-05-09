from pathlib import Path
from abc import ABC, abstractmethod
from kreuzberg import (
    extract_file,
    ExtractionConfig,
    OcrConfig,
)
from core.common.utils import get_llm


class Extractor(ABC):
    def __init__(self):
        self.llm = get_llm()

    @abstractmethod
    async def summarize_with_vlm(
        self,
        source: Path,
        context: str = "",
    ) -> str:
        pass

    async def extract_with_ocr(
        self,
        source: Path,
        languages: list[str],
    ) -> str:
        config: ExtractionConfig = ExtractionConfig(
            use_cache=True,
            ocr=OcrConfig(
                backend="paddleocr",
                language="+".join(languages),
                paddle_ocr_config={"model_tier": "server"},
            ),
        )
        text = await extract_file(source, config=config)
        return text.content
