import os
import re
import shutil
import tempfile
from pathlib import Path

import httpx
from mineru.cli.common import image_suffixes, office_suffixes, pdf_suffixes
from mineru.cli import api_client
from mineru.cli.visualization import (
    VisualizationJob,
    run_visualization_job,
)
from mineru.utils.guess_suffix_or_lang import guess_suffix_by_path

from common.logger import get_logger
from processors.image_extractor import ImageExtractor

logger = get_logger(__name__)

_SUPPORTED_INPUT_SUFFIXES = set(pdf_suffixes + image_suffixes + office_suffixes)

# Image types MinerU may emit in its output (jpg for pdf pages, png for office docs)
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def _sanitize_stem(file_path: Path) -> str:
    """Return a filesystem-safe stem for the given file.

    Windows cannot create directories whose names end with spaces or dots,
    and MinerU uses the file stem as an output directory name. Strip such
    trailing characters so parsing does not fail server-side.
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


def _build_configuration(effort: str) -> dict[str, str | list[str]]:
    return api_client.build_parse_request_form_data(
        lang_list=["ch"],
        backend="hybrid-engine",
        parse_method="auto",
        effort=effort,
        formula_enable=True,
        table_enable=True,
        image_analysis=False,
        server_url=None,
        start_page_id=0,
        end_page_id=None,
        return_md=True,
        return_images=True,
        return_middle_json=True,
        return_model_output=False,
        return_content_list=False,
        response_format_zip=True,
        return_original_file=True,
    )


def _format_status_message(status_snapshot: api_client.TaskStatusSnapshot) -> str:
    if status_snapshot.queued_ahead is None:
        return status_snapshot.status
    return f"{status_snapshot.status} (queued_ahead={status_snapshot.queued_ahead})"


class MineruExtractor:
    def __init__(self):
        self.api_url = os.environ.get("MINERU_API_URL")
        self.image_extractor = ImageExtractor()

    async def extract(
        self,
        input_path: str | Path,
        output_dir: str | Path,
        *,
        effort: str = "high",
    ) -> None:
        logger.info(f"Extracting text with MinerU for {input_path}")
        input_files = _collect_input_files(input_path)
        output_path = Path(output_dir).expanduser().resolve()
        output_path.mkdir(parents=True, exist_ok=True)

        configuration = _build_configuration(effort)

        upload_assets = [
            api_client.UploadAsset(
                path=file_path,
                upload_name=f"{_sanitize_stem(file_path)}{file_path.suffix}",
            )
            for file_path in input_files
        ]

        result_zip_path: Path | None = None
        task_label = f"{len(input_files)} file(s)"

        async with httpx.AsyncClient(
            timeout=api_client.build_http_timeout(),
            follow_redirects=True,
        ) as http_client:
            try:
                server_health = await api_client.fetch_server_health(
                    http_client,
                    api_client.normalize_base_url(self.api_url),
                )

                logger.info(
                    f"Submitting {len(upload_assets)} file(s) to {server_health.base_url}"
                )

                submit_response = await api_client.submit_parse_task(
                    base_url=server_health.base_url,
                    upload_assets=upload_assets,
                    form_data=configuration,
                )

                logger.info(f"task_id: {submit_response.task_id}")
                if submit_response.queued_ahead is not None:
                    logger.info(
                        f"status: pending (queued_ahead={submit_response.queued_ahead})"
                    )

                last_status_message = None

                def on_status_update(
                    status_snapshot: api_client.TaskStatusSnapshot,
                ) -> None:
                    nonlocal last_status_message
                    message = _format_status_message(status_snapshot)
                    if message == last_status_message:
                        return
                    last_status_message = message
                    logger.info(f"status: {message}")

                await api_client.wait_for_task_result(
                    client=http_client,
                    submit_response=submit_response,
                    task_label=task_label,
                    status_snapshot_callback=on_status_update,
                )
                logger.info("status: completed")

                result_zip_path = await api_client.download_result_zip(
                    client=http_client,
                    submit_response=submit_response,
                    task_label=task_label,
                )
            except Exception as e:
                logger.error(f"Error extracting text with MinerU: {e}")
                raise e

        assert result_zip_path is not None
        try:
            with tempfile.TemporaryDirectory(prefix="mineru-extract-") as tmp_dir:
                extract_dir = Path(tmp_dir)
                api_client.safe_extract_zip(result_zip_path, extract_dir)

                self._flatten_document_dirs(extract_dir, input_files)
                self._generate_layout_pdfs(extract_dir)
                await self._process_all_images(extract_dir, input_files)

                self._move_results(extract_dir, output_path, input_files)
        finally:
            result_zip_path.unlink(missing_ok=True)

        logger.info(f"Extracted result to: {output_path}")

    @staticmethod
    def _move_results(
        extract_dir: Path, output_path: Path, input_files: list[Path]
    ) -> None:
        """Move processed document directories from the temp dir to the output dir."""
        for input_file in input_files:
            document_stem = _sanitize_stem(input_file)
            source_dir = extract_dir / document_stem
            if not source_dir.is_dir():
                logger.warning(f"No extracted result for {input_file.name}")
                continue
            target_dir = output_path / document_stem
            if target_dir.exists():
                shutil.rmtree(target_dir)
            shutil.move(str(source_dir), str(target_dir))

    async def _process_all_images(
        self, extract_dir: Path, input_files: list[Path]
    ) -> None:
        """Run image processing for each document extracted in this run."""
        for input_file in input_files:
            document_stem = _sanitize_stem(input_file)
            document_output_dir = extract_dir / document_stem
            markdown_files = sorted(document_output_dir.rglob(f"{document_stem}.md"))
            if not markdown_files:
                logger.warning(
                    f"No markdown output found for {input_file.name} in: {document_output_dir}"
                )
                continue
            for markdown_file in markdown_files:
                logger.info(f"Processing images for: {markdown_file}")
                await self._parse_images(markdown_file)

    async def _parse_images(self, markdown_file: Path) -> None:
        """Replace image references in one markdown file with extracted content.

        Images whose content can be fully captured as text are inlined and the
        image file is removed. Lossy images are kept on disk (renamed by ID) and
        referenced via an <Image> tag.
        """
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

    @staticmethod
    def _flatten_document_dirs(extract_dir: Path, input_files: list[Path]) -> None:
        """Remove the intermediate backend/method directory from MinerU output.

        MinerU extracts to {output}/{stem}/{backend_method}/ (e.g. hybrid_auto);
        move its contents up so files live directly in {output}/{stem}/.
        """
        for input_file in input_files:
            document_stem = _sanitize_stem(input_file)
            document_dir = extract_dir / document_stem
            if not document_dir.is_dir():
                continue
            markdown_files = sorted(document_dir.rglob(f"{document_stem}.md"))
            for markdown_file in markdown_files:
                intermediate_dir = markdown_file.parent
                if intermediate_dir == document_dir:
                    continue  # already flat
                for item in list(intermediate_dir.iterdir()):
                    target = document_dir / item.name
                    if target.exists():
                        if target.is_dir():
                            shutil.rmtree(target)
                        else:
                            target.unlink()
                    shutil.move(str(item), str(target))
                intermediate_dir.rmdir()

    @staticmethod
    def _generate_layout_pdfs(extract_dir: Path) -> None:
        """Draw {stem}_layout.pdf locally from middle.json + origin.pdf."""
        for middle_json_path in sorted(extract_dir.rglob("*_middle.json")):
            document_stem = middle_json_path.name.removesuffix("_middle.json")
            origin_pdf_path = middle_json_path.parent / f"{document_stem}_origin.pdf"
            if origin_pdf_path.is_file():
                run_visualization_job(
                    VisualizationJob(
                        document_stem=document_stem,
                        backend="hybrid-engine",
                        parse_method="auto",
                        parse_dir=middle_json_path.parent,
                        draw_span=False,
                    )
                )

            # Remove the middle.json and any origin file (pdf, xlsx, docx, ...)
            middle_json_path.unlink(missing_ok=True)
            for origin_file in middle_json_path.parent.glob(
                f"{document_stem}_origin.*"
            ):
                origin_file.unlink(missing_ok=True)
