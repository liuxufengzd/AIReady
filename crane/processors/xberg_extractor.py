from pathlib import Path

from xberg import (
    ExtractInput,
    ExtractionConfig,
    LayoutDetectionConfig,
    OcrConfig,
    PdfConfig,
    SecurityLimits,
    extract,
)

from common.logger import get_logger

logger = get_logger(__name__)

_SUPPORTED_INPUT_SUFFIXES = {
    # documents
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".csv",
    # markup / plain text
    ".html",
    ".htm",
    ".md",
    ".txt",
    ".rst",
    # images (parsed via OCR)
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".tiff",
    ".webp",
}


def _sanitize_stem(file_path: Path) -> str:
    """Return a filesystem-safe stem for the given file.

    Windows cannot create directories whose names end with spaces or dots,
    strip such trailing characters so writing output does not fail.
    """
    return file_path.stem.rstrip(" .") or file_path.stem


def _collect_input_files(input_path: str | Path) -> list[Path]:
    path = Path(input_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Input path does not exist: {path}")

    if path.is_file():
        if path.suffix.lower() not in _SUPPORTED_INPUT_SUFFIXES:
            raise ValueError(f"Unsupported input file type: {path.name}")
        return [path]

    if not path.is_dir():
        raise ValueError(f"Input path must be a file or directory: {path}")

    input_files = []
    for candidate in sorted(path.iterdir(), key=lambda item: item.name):
        if not candidate.is_file():
            continue
        if candidate.suffix.lower() not in _SUPPORTED_INPUT_SUFFIXES:
            logger.warning(f"Skipping unsupported file type: {candidate.name}")
            continue
        input_files.append(candidate.resolve())
    if not input_files:
        raise ValueError(f"No supported files found in directory: {path}")
    return input_files


def _build_configuration() -> ExtractionConfig:
    security_limits = SecurityLimits(max_content_size=512 * 1024 * 1024)
    return ExtractionConfig(
        output_format="markdown",
        force_ocr=True,
        security_limits=security_limits,
        ocr=OcrConfig(
            backend="paddleocr",
            backend_options={
                "model_version": "pp-ocrv6",
                "model_tier": "medium",
            },
            # The image-decoding path for OCR reads its limits from OcrConfig,
            # not from the top-level ExtractionConfig.
            security_limits=security_limits,
        ),
        # Layout detection is disabled by default in xberg; enabling it fixes
        # multi-column reading order and routes table/formula regions to
        # dedicated recognition models (SLANet for tables, LaTeX-OCR for
        # formulas).
        layout=LayoutDetectionConfig(
            strategy="always",
            table_model="slanet_auto",
            formula_model="latex_ocr",
        ),
        pdf_options=PdfConfig(reading_order=True),
    )


# Compared to MinerU, precision, completeness, faithfulness are worse but is faster and supports more file types.
# For files that contain complex table, layout, formula, or image, MinerU is recommended.
class XbergExtractor:
    async def extract(
        self,
        input_path: str | Path,
        output_dir: str | Path,
    ) -> None:
        logger.info(f"Extracting text with Xberg for {input_path}")
        input_files = _collect_input_files(input_path)
        output_path = Path(output_dir).expanduser().resolve()
        output_path.mkdir(parents=True, exist_ok=True)

        configuration = _build_configuration()

        for input_file in input_files:
            try:
                logger.info(f"Parsing: {input_file.name}")
                envelope = await extract(
                    ExtractInput(kind="uri", uri=str(input_file)),
                    configuration,
                )
                if envelope.errors:
                    messages = "; ".join(str(error) for error in envelope.errors)
                    raise RuntimeError(f"Xberg reported errors: {messages}")
                if not envelope.results:
                    raise RuntimeError("Xberg returned no extracted document")
                document = envelope.results[0]

                # It is not a completeness or recall score
                if document.quality_score is not None:
                    logger.info(
                        f"Quality score for {input_file.name}: "
                        f"{document.quality_score:.3f}"
                    )
                if document.processing_warnings:
                    for warning in document.processing_warnings:
                        logger.warning(
                            f"Processing warning for {input_file.name}: {warning}"
                        )

                document_stem = _sanitize_stem(input_file)
                document_dir = output_path / document_stem
                document_dir.mkdir(parents=True, exist_ok=True)

                markdown_file = document_dir / f"{document_stem}.md"
                markdown_file.write_text(document.content, encoding="utf-8")
                logger.info(f"Wrote markdown: {markdown_file}")
            except Exception as e:
                logger.error(f"Error extracting {input_file.name} with Xberg: {e}")
                raise e

        logger.info(f"Extracted result to: {output_path}")
