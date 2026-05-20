from pathlib import Path
from kreuzberg import (
    extract_file,
    ExtractionConfig,
    OcrConfig,
)
from common.logger import get_logger

logger = get_logger(__name__)


class OCRExtractor:
    async def extract(
        self,
        source: Path,
        languages: list[str],
    ) -> str:
        logger.info(f"Extracting OCR text with PaddleOCR for file: {source}")
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
