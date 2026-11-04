from pathlib import Path

import xberg

from parsers.parser_base import ParserBase


class XbergParser(ParserBase):
    def __init__(
        self,
        language: list[str] = ["jpn", "eng"],
    ):
        self._config = xberg.ExtractionConfig(
            output_format="markdown",
            layout=xberg.LayoutDetectionConfig(
                strategy="always", table_model="slanet_auto"
            ),
            use_layout_for_markdown=True,
            ocr=xberg.OcrConfig(
                backend="paddle-ocr",
                language=language,
                paddle_ocr_settings=xberg.PaddleOcrConfig(
                    model_tier="server",
                    enable_table_detection=True,
                ),
            ),
        )

    async def parse(self, file_path: Path) -> str:
        file_path = Path(file_path)
        result = await xberg.extract(
            xberg.ExtractInput(uri=str(file_path.resolve())), self._config
        )
        if result.errors:
            raise RuntimeError(
                f"xberg failed to parse {file_path.name}: {result.errors}"
            )
        return "\n\n".join(document.content for document in result.results)
