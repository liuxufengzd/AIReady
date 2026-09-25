"""Turn a prepared PDF or image into the files a reviewer will accept.

These functions write only inside the document's ``tmp`` directory. Review
later copies the accepted files up to ``processed/{project}/{filename}/`` and
deletes ``tmp``.
"""

import os
import re
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from dataprep.image_extractor import ImageExtractor
from docvortex.document.pdf import PDFDocument
from docvortex.visualization import render_layout_pdf
from mineru.filetypes import IMAGE_EXTENSIONS
from mineru.parser import MinerUApiParser, ParseResult
from mineru.parser.file_type import guess_suffix_by_path
from mineru.parser.writer import FileBasedDataWriter

MARKDOWN_NAME = "markdown.md"
LAYOUT_NAME = "layout.pdf"
MIDDLE_JSON_NAME = "middle_json.json"
IMAGES_DIRNAME = "images"

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff"}


def _prepared_parse_path(source: Path, tmp_dir: Path) -> Path:
    """Copy to a sanitized filename when the stem is unsafe on Windows."""
    sanitized = source.stem.rstrip(" .") or source.stem
    if sanitized == source.stem:
        return source
    target = tmp_dir / f"{sanitized}{source.suffix}"
    shutil.copy2(source, target)
    return target


async def parse_with_mineru(
    source: Path,
    log: Callable[[str], None] | None = None,
) -> None:
    """Parse ``source`` with MinerU."""
    if not source.is_file():
        raise FileNotFoundError(f"Parse source not found: {source}")

    tmp_dir = source.parent
    parser = MinerUApiParser(
        api_url=os.environ.get("MINERU_API_URL"),
        tier="standard",
        include_images=True,
    )

    last_status = None

    def on_status(status: object) -> None:
        nonlocal last_status
        if status == last_status or log is None:
            return
        last_status = status
        log(f"{source.name}: status={status}")

    with tempfile.TemporaryDirectory(prefix="mineru-parse-") as tmp:
        parse_path = _prepared_parse_path(source, Path(tmp))
        result = await parser.parse_async(parse_path, status_callback=on_status)
    result.save(FileBasedDataWriter(str(tmp_dir)))
    if not (tmp_dir / MARKDOWN_NAME).is_file():
        raise FileNotFoundError(f"MinerU did not write {MARKDOWN_NAME} in {tmp_dir}")


def _layout_source_pdf(source: Path) -> bytes | None:
    """Return the PDF MinerU laid out, converting images the same way it does."""
    suffix = guess_suffix_by_path(source)
    file_bytes = source.read_bytes()
    if suffix == "pdf":
        return file_bytes
    if suffix in IMAGE_EXTENSIONS:
        return PDFDocument.from_image(file_bytes).bytes
    return None


def write_layout(source: Path, log: Callable[[str], None] | None = None) -> bool:
    """Draw detected regions onto ``source``. Return True if successful."""
    destination = source.parent / LAYOUT_NAME
    try:
        middle_path = source.parent / MIDDLE_JSON_NAME
        if not middle_path.is_file():
            raise FileNotFoundError(f"Middle JSON not found: {middle_path}")
        source_pdf = _layout_source_pdf(source)
        if source_pdf is None:
            raise ValueError(f"{source.name} is not a PDF or image")
        result = ParseResult.from_json(middle_path.read_text(encoding="utf-8"))
        destination.write_bytes(render_layout_pdf(source_pdf, result.pages))
        return True
    except Exception as exc:
        if log is not None:
            log(f"Skipping layout PDF: {exc}")
        destination.unlink(missing_ok=True)
        return False


async def extract_images(source: Path) -> int:
    """Inline image text, keep lossy images, and drop MinerU JSON."""
    directory = source.parent
    markdown_file = directory / MARKDOWN_NAME
    if not markdown_file.is_file():
        raise FileNotFoundError(f"Markdown file not found: {markdown_file}")
    text = markdown_file.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError(f"Markdown output is empty: {markdown_file}")

    image_store = directory / IMAGES_DIRNAME
    image_files = sorted(
        path
        for path in image_store.rglob("*")
        if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES
    )
    image_id = 1
    retained = 0
    for image_file in image_files:
        escaped_stem = re.escape(image_file.stem)
        pattern = re.compile(
            r"!\[[^\]]*\]\([^)]*" + escaped_stem + r"[^)]*\)"
            r"|<img\b[^>]*" + escaped_stem + r"[^>]*>"
        )
        if not pattern.search(text):
            image_file.unlink(missing_ok=True)
            continue

        image_meta = await ImageExtractor().extract(image_file)
        content = getattr(image_meta, "content", None)
        if not isinstance(content, str):
            raise TypeError(
                f"Image extraction for {image_file.name} returned no content"
            )
        if getattr(image_meta, "info_loss", False):
            stored = image_store / f"{image_id}{image_file.suffix.lower()}"
            if stored.exists():
                stored.unlink()
            shutil.move(image_file, stored)
            image_tag = (
                "<Image>\n"
                f"  <ID>{image_id}</ID>\n"
                f"  <Content>{content}</Content>\n"
                "</Image>"
            )
            image_id += 1
            retained += 1
        else:
            image_tag = content
            image_file.unlink(missing_ok=True)
        text = pattern.sub(lambda _, tag=image_tag: tag + "\n", text)

    if image_store.exists() and not any(image_store.rglob("*")):
        shutil.rmtree(image_store, ignore_errors=True)
    markdown_file.write_text(text, encoding="utf-8")
    for path in directory.glob("*.json"):
        path.unlink(missing_ok=True)
    return retained
