from functools import cached_property
import shutil
import tempfile
import dagster as dg
from collections.abc import Callable
from pathlib import Path
from typing_extensions import override

from dataprep.resources.base_parser import BaseParser
from docvortex.document.pdf import PDFDocument
from docvortex.visualization import render_layout_pdf
from mineru.parser import MinerUApiParser, ParseResult
from mineru.parser.file_type import guess_suffix_by_path
from mineru.parser.writer import FileBasedDataWriter
from dataprep.common.const import MARKDOWN_NAME, IMAGE_EXTENSIONS

_LAYOUT_NAME = "layout.pdf"
_MIDDLE_JSON_NAME = "middle_json.json"


class MinerUParser(BaseParser, dg.ConfigurableResource):
    """Parse a prepared PDF or image through the MinerU API.

    Writes markdown, images, and middle JSON beside the source.
    ``write_layout`` draws detected regions onto ``layout.pdf``.
    """

    api_url: str
    tier: str = "standard"

    @cached_property
    def client(self) -> MinerUApiParser:
        return MinerUApiParser(
            api_url=self.api_url,
            tier=self.tier,
            include_images=True,
        )

    @override
    async def parse_async(
        self, source: Path, logger: Callable[[str], None] | None = None
    ) -> str:
        """Parse the document file and return the text content.

        Args:
            source: The path to the document file.
            logger: The callback function to log the progress.
        Returns:
            The text content of the document.
        """
        if not source.is_file():
            raise FileNotFoundError(f"Document file not found: {source}")

        tmp_dir = source.parent
        last_status = None

        def on_status(status: object) -> None:
            nonlocal last_status
            if status == last_status or logger is None:
                return
            last_status = status
            logger(f"{source.name}: status={status}")

        with tempfile.TemporaryDirectory(prefix="mineru-parse-") as tmp:
            parse_path = self._prepared_parse_path(source, Path(tmp))
            result = await self.client.parse_async(
                parse_path, status_callback=on_status
            )
        result.save(FileBasedDataWriter(str(tmp_dir)))
        if not (tmp_dir / MARKDOWN_NAME).is_file():
            raise FileNotFoundError(
                f"MinerU did not write {MARKDOWN_NAME} in {tmp_dir}"
            )
        return result.markdown

    def write_layout(
        self, source: Path, logger: Callable[[str], None] | None = None
    ) -> bool:
        """Draw detected regions onto the document file.

        Args:
            source: The path to the document file.
            logger: The callback function to log the progress.
        Returns:
            True if successful, False otherwise.
        """
        destination = source.parent / _LAYOUT_NAME
        try:
            middle_path = source.parent / _MIDDLE_JSON_NAME
            if not middle_path.is_file():
                raise FileNotFoundError(f"Middle JSON not found: {middle_path}")
            source_pdf = self._layout_source_pdf(source)
            if source_pdf is None:
                raise ValueError(f"{source.name} is not a PDF or image")
            result = ParseResult.from_json(middle_path.read_text(encoding="utf-8"))
            destination.write_bytes(render_layout_pdf(source_pdf, result.pages))
            return True
        except Exception as exc:
            if logger is not None:
                logger(f"Skipping layout PDF: {exc}")
            destination.unlink(missing_ok=True)
            return False

    def _prepared_parse_path(self, source: Path, tmp_dir: Path) -> Path:
        """Copy to a sanitized filename when the stem is unsafe on Windows."""
        sanitized = source.stem.rstrip(" .") or source.stem
        if sanitized == source.stem:
            return source
        target = tmp_dir / f"{sanitized}{source.suffix}"
        shutil.copy2(source, target)
        return target

    def _layout_source_pdf(self, source: Path) -> bytes | None:
        """Return the PDF MinerU laid out, converting images the same way it does."""
        suffix = guess_suffix_by_path(source)
        file_bytes = source.read_bytes()
        if suffix == "pdf":
            return file_bytes
        if f".{suffix}" in IMAGE_EXTENSIONS:
            return PDFDocument.from_image(file_bytes).bytes
        return None
