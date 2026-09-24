import os
import re
import shutil
import tempfile
from pathlib import Path

from docvortex.document.pdf import PDFDocument
from docvortex.visualization import render_layout_pdf
from mineru.filetypes import IMAGE_EXTENSIONS, PARSEABLE_EXTENSIONS
from mineru.parser import ApiJobStatus, MinerUApiParser, ParseResult, Tier
from mineru.parser.file_type import guess_suffix_by_path
from mineru.parser.writer import FileBasedDataWriter

from common.logger import get_logger
from processors.image_extractor import ImageExtractor

logger = get_logger(__name__)

_SUPPORTED_INPUT_SUFFIXES = set(PARSEABLE_EXTENSIONS)

# Image types MinerU may emit in its output (jpg for pdf pages, png for office docs)
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff"}


def _sanitize_stem(file_path: Path) -> str:
    """Return a filesystem-safe stem for the given file.

    Windows cannot create directories whose names end with spaces or dots,
    and MinerU-derived output uses the file stem as a directory name. Strip
    such trailing characters so writing results does not fail.
    """
    return file_path.stem.rstrip(" .") or file_path.stem


def _collect_input_files(input_path: str | Path) -> list[Path]:
    path = Path(input_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Input path does not exist: {path}")

    if path.is_file():
        file_suffix = guess_suffix_by_path(path)
        if file_suffix not in _SUPPORTED_INPUT_SUFFIXES:
            raise ValueError(f"Unsupported input file type: {path.name}")
        return [path]

    if not path.is_dir():
        raise ValueError(f"Input path must be a file or directory: {path}")

    input_files = []
    for candidate in sorted(path.iterdir(), key=lambda item: item.name):
        if not candidate.is_file():
            continue
        if guess_suffix_by_path(candidate) not in _SUPPORTED_INPUT_SUFFIXES:
            logger.warning(f"Skipping unsupported file type: {candidate.name}")
            continue
        input_files.append(candidate.resolve())
    if not input_files:
        raise ValueError(f"No supported files found in directory: {path}")
    return input_files


def _layout_source_pdf(source_path: Path) -> bytes | None:
    """Return the PDF MinerU laid out, converting images the same way it does."""
    suffix = guess_suffix_by_path(source_path)
    file_bytes = source_path.read_bytes()
    if suffix == "pdf":
        return file_bytes
    if suffix in IMAGE_EXTENSIONS:
        return PDFDocument.from_image(file_bytes).bytes
    return None


def _prepared_parse_path(file_path: Path, tmp_dir: Path) -> Path:
    """Copy to a sanitized filename when the stem is unsafe on Windows."""
    sanitized = _sanitize_stem(file_path)
    if sanitized == file_path.stem:
        return file_path
    target = tmp_dir / f"{sanitized}{file_path.suffix}"
    shutil.copy2(file_path, target)
    return target


class MineruExtractor:
    def __init__(self):
        self.api_url = os.environ.get("MINERU_API_URL")
        self.image_extractor = ImageExtractor()

    async def extract(
        self,
        input_path: str | Path,
        output_dir: str | Path,
        *,
        tier: Tier = "standard",
    ) -> None:
        """Parse documents via a self-hosted MinerU V1 API and write results.

        Uses ``MinerUApiParser`` (upload → job → poll → download). Reuses one
        parser instance across files. ``tier`` selects quality
        (``flash`` / ``basic`` / ``standard`` / ``advanced``).

        ``basic``: Uses a specialized small-model pipeline throughout. Errors
        are mechanical (missed recognition, garbled text, layout
        misalignment). Suited to financial reports and invoices, standard
        contracts, official documents, and low-compute edge devices —
        scenarios with extremely low error tolerance and highly fixed formats.

        ``standard``: A specialized small model analyzes layout (introducing
        some mechanical errors), while a VLM analyzes blocks (logical errors
        such as hallucinations). Suited to academic papers, complex textbooks,
        old scanned literature, handwriting, and difficult charts — scenarios
        with extremely complex typesetting where overall coherence is the goal.

        ``advanced``: Uses a VLM throughout (eliminates mechanical errors, but
        may introduce more hallucinations). Try this when tier 2 results are
        unsatisfactory. Currently, advanced is less precise than standard for
        many cases.
        """
        logger.info(f"Extracting text with MinerU for {input_path}")
        input_files = _collect_input_files(input_path)
        output_path = Path(output_dir).expanduser().resolve()
        output_path.mkdir(parents=True, exist_ok=True)

        parser = MinerUApiParser(
            api_url=self.api_url,
            tier=tier,
            include_images=True,
        )

        failures: list[tuple[str, str]] = []
        with tempfile.TemporaryDirectory(prefix="mineru-extract-") as tmp_dir:
            prepare_dir = Path(tmp_dir)
            for input_file in input_files:
                document_stem = _sanitize_stem(input_file)
                document_dir = output_path / document_stem
                try:
                    parse_path = _prepared_parse_path(input_file, prepare_dir)
                    logger.info(
                        f"Submitting {input_file.name} to MinerU "
                        f"(tier={tier}, api_url={self.api_url})"
                    )

                    last_status: ApiJobStatus | None = None

                    def on_status(status: ApiJobStatus) -> None:
                        nonlocal last_status
                        if status == last_status:
                            return
                        last_status = status
                        logger.info(f"{input_file.name}: status={status}")

                    result = await parser.parse_async(
                        parse_path,
                        status_callback=on_status,
                    )
                    logger.info(f"{input_file.name}: status=completed")

                    if document_dir.exists():
                        shutil.rmtree(document_dir)
                    document_dir.mkdir(parents=True, exist_ok=True)

                    result.save(FileBasedDataWriter(str(document_dir)))
                    self._normalize_saved_artifacts(
                        document_dir, document_stem, result, source_path=input_file
                    )
                    await self._parse_images(document_dir / f"{document_stem}.md")
                except Exception as exc:
                    failures.append((input_file.name, str(exc)))
                    logger.error(f"failed: {input_file.name}: {exc}")
                    if document_dir.exists():
                        shutil.rmtree(document_dir, ignore_errors=True)

        if failures:
            summary = "; ".join(f"{name}: {err}" for name, err in failures)
            raise RuntimeError(
                f"{len(failures)} of {len(input_files)} file(s) failed: {summary}"
            )

        logger.info(f"Extracted result to: {output_path}")

    @staticmethod
    def _normalize_saved_artifacts(
        document_dir: Path,
        document_stem: str,
        result: ParseResult,
        source_path: Path,
    ) -> None:
        """Rename markdown, draw layout PDF, drop intermediate SDK artifacts."""
        markdown_path = document_dir / "markdown.md"
        target_markdown = document_dir / f"{document_stem}.md"
        if markdown_path.is_file():
            markdown_path.replace(target_markdown)
        else:
            raise FileNotFoundError(f"Markdown file not found: {markdown_path}")

        try:
            source_pdf = _layout_source_pdf(source_path)
            if source_pdf is None:
                logger.warning(
                    f"Skipping layout PDF for {document_stem}: "
                    "source is not a PDF or image"
                )
            else:
                layout_pdf = render_layout_pdf(source_pdf, result.middle_json.pages)
                (document_dir / f"{document_stem}_layout.pdf").write_bytes(layout_pdf)
        except Exception as exc:
            logger.warning(f"Skipping layout PDF for {document_stem}: {exc}")

        for name in (
            "middle_json.json",
            "structured_content.json",
        ):
            (document_dir / name).unlink(missing_ok=True)

    async def _parse_images(self, markdown_file: Path) -> None:
        """Replace image references in one markdown file with extracted content.

        Images whose content can be fully captured as text are inlined and the
        image file is removed. Lossy images are kept on disk (renamed by ID) and
        referenced via an <Image> tag.
        """
        if not markdown_file.is_file():
            logger.warning(f"No markdown output found at: {markdown_file}")
            return

        text = markdown_file.read_text(encoding="utf-8")
        document_dir = markdown_file.parent
        image_store_dir = document_dir / "images"

        image_id = 1
        image_files = sorted(
            path
            for path in document_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES
        )
        for image_file in image_files:
            # replace ![...](... file_name ...) or <img src="... file_name ..."/>
            # with an <Image .../> tag (tables use the HTML <img> form)
            escaped_stem = re.escape(image_file.stem)
            pattern = re.compile(
                r"!\[[^\]]*\]\([^)]*" + escaped_stem + r"[^)]*\)"
                r"|<img\b[^>]*" + escaped_stem + r"[^>]*>"
            )
            if not pattern.search(text):
                image_file.unlink(missing_ok=True)
                continue

            image_meta = await self.image_extractor.extract(image_file)
            if image_meta.info_loss:
                # keep the original image, renamed by ID, and reference it
                image_store_dir.mkdir(parents=True, exist_ok=True)
                stored_image_path = (
                    image_store_dir / f"{image_id}{image_file.suffix.lower()}"
                )
                if image_file.resolve() != stored_image_path.resolve():
                    shutil.move(image_file, stored_image_path)
                image_tag = (
                    "<Image>\n"
                    f"  <ID>{image_id}</ID>\n"
                    f"  <Content>{image_meta.content}</Content>\n"
                    "</Image>"
                )
                image_id += 1
            else:
                # content fully captured as text; drop the image file
                image_tag = image_meta.content
                image_file.unlink(missing_ok=True)
            text = pattern.sub(lambda _match: image_tag + "\n", text)

        markdown_file.write_text(text, encoding="utf-8")
