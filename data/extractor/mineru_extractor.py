import os
import re
import shutil
import tempfile
from pathlib import Path

from docvortex.document.pdf import PDFDocument
from docvortex.visualization import render_layout_pdf
from mineru.filetypes import IMAGE_EXTENSIONS
from mineru.parser import ApiJobStatus, MinerUApiParser, ParseResult, Tier
from mineru.parser.file_type import guess_suffix_by_path
from mineru.parser.writer import FileBasedDataWriter

from common.logger import get_logger
from data.common.store_paths import review_artifact_dir
from data.extractor.image_extractor import ImageExtractor

logger = get_logger(__name__)

# Image types MinerU may emit in its output (jpg for pdf pages, png for office docs)
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff"}
_MARKDOWN_NAME = "markdown.md"
_LAYOUT_NAME = "layout.pdf"
_IMAGES_DIRNAME = "images"


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
    sanitized = file_path.stem.rstrip(" .") or file_path.stem
    if sanitized == file_path.stem:
        return file_path
    target = tmp_dir / f"{sanitized}{file_path.suffix}"
    shutil.copy2(file_path, target)
    return target


def _write_layout_pdf(source_path: Path, result: ParseResult, dest: Path) -> None:
    """Draw detected regions onto the source and write them to disk.

    Failures leave the parse usable. The PDF bytes are not retained after the write.
    """
    try:
        source_pdf = _layout_source_pdf(source_path)
        if source_pdf is None:
            logger.warning(
                f"Skipping layout PDF for {source_path.name}: "
                "source is not a PDF or image"
            )
            return
        dest.write_bytes(render_layout_pdf(source_pdf, result.middle_json.pages))
    except Exception as exc:
        logger.warning(f"Skipping layout PDF for {source_path.name}: {exc}")
        dest.unlink(missing_ok=True)


class MineruExtractor:
    def __init__(self):
        self.api_url = os.environ.get("MINERU_API_URL")
        self.image_extractor = ImageExtractor()

    async def extract(
        self,
        project: str,
        source: Path,
        *,
        tier: Tier = "standard",
    ) -> None:
        """Parse one document via a self-hosted MinerU V1 API.

        Writes ``markdown.md``, lossy images, and ``layout.pdf`` under
        ``store/tmp/{project}/{filename}/``.

        ``tier``: selects quality (``flash`` / ``basic`` / ``standard`` / ``advanced``).

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
        out_dir = review_artifact_dir(project, source.stem)
        logger.info(
            f"Extracting text with MinerU for file: {source} "
            f"(tier={tier}, review_dir={out_dir})"
        )
        source_path = source.expanduser().resolve()
        if not source_path.is_file():
            raise FileNotFoundError(f"Input file does not exist: {source_path}")

        parser = MinerUApiParser(
            api_url=self.api_url,
            tier=tier,
            include_images=True,
        )

        with tempfile.TemporaryDirectory(prefix="mineru-extract-") as tmp_dir:
            prepare_dir = Path(tmp_dir)
            parse_path = _prepared_parse_path(source_path, prepare_dir)
            logger.info(
                f"Submitting {source_path.name} to MinerU "
                f"(tier={tier}, api_url={self.api_url})"
            )

            last_status: ApiJobStatus | None = None

            def on_status(status: ApiJobStatus) -> None:
                nonlocal last_status
                if status == last_status:
                    return
                last_status = status
                logger.info(f"{source_path.name}: status={status}")

            try:
                result = await parser.parse_async(
                    parse_path,
                    status_callback=on_status,
                )
            except Exception as exc:
                logger.error(f"Error extracting text with MinerU: {exc}")
                raise

            if out_dir.exists():
                shutil.rmtree(out_dir)
            out_dir.mkdir(parents=True)
            try:
                result.save(FileBasedDataWriter(str(out_dir)))
                _write_layout_pdf(source_path, result, out_dir / _LAYOUT_NAME)
                await self._parse_images(out_dir)
                for extra in out_dir.glob("*.json"):
                    extra.unlink(missing_ok=True)
            except Exception:
                shutil.rmtree(out_dir, ignore_errors=True)
                raise

    async def _parse_images(self, extract_dir: Path) -> None:
        """Replace image references in the saved markdown and write it back.

        Images whose content can be fully captured as text are inlined and the
        image file is removed. Lossy images stay in this review directory
        (renamed by ID) and are referenced via an <Image> tag.
        """
        markdown_file = extract_dir / _MARKDOWN_NAME
        if not markdown_file.is_file():
            raise FileNotFoundError(f"Markdown file not found: {markdown_file}")

        text = markdown_file.read_text(encoding="utf-8")
        if not text.strip():
            raise ValueError(f"Markdown output is empty: {markdown_file}")
        image_id = 1
        image_store_dir = extract_dir / _IMAGES_DIRNAME
        image_files = sorted(
            path
            for path in extract_dir.rglob("*")
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
                image_store_dir.mkdir(parents=True, exist_ok=True)
                stored_image_path = (
                    image_store_dir / f"{image_id}{image_file.suffix.lower()}"
                )
                if image_file.resolve() != stored_image_path.resolve():
                    if stored_image_path.exists():
                        stored_image_path.unlink()
                    shutil.move(image_file, stored_image_path)
                image_tag = (
                    "<Image>\n"
                    f"  <ID>{image_id}</ID>\n"
                    f"  <Content>{image_meta.content}</Content>\n"
                    "</Image>"
                )
                image_id += 1
            else:
                image_tag = image_meta.content
                image_file.unlink(missing_ok=True)
            text = pattern.sub(lambda _, tag=image_tag: tag + "\n", text)

        # if all images are inlined, remove the image store directory
        if not any(image_store_dir.rglob("*")):
            shutil.rmtree(image_store_dir, ignore_errors=True)
        markdown_file.write_text(text, encoding="utf-8")
