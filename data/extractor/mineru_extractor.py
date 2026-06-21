import os
import re
import tempfile
from pathlib import Path
import httpx
from mineru.cli import api_client
import shutil

from common.logger import get_logger
from data.common import const
from data.extractor.image_extractor import ImageExtractor

logger = get_logger(__name__)


def _build_form_data(languages: list[str]) -> dict[str, str | list[str]]:
    return api_client.build_parse_request_form_data(
        lang_list=languages,
        backend="hybrid-auto-engine",
        parse_method="auto",
        formula_enable=True,
        table_enable=True,
        image_analysis=True,
        server_url=None,
        start_page_id=0,
        end_page_id=None,
        return_md=True,
        return_images=True,
        return_middle_json=False,
        return_model_output=False,
        return_content_list=False,
        response_format_zip=True,
        return_original_file=False,
    )


def _read_extracted_markdown(extract_dir: Path) -> str:
    markdown_files = sorted(extract_dir.rglob("*.md"))
    if not markdown_files:
        raise ValueError(f"No markdown output found in extracted result: {extract_dir}")

    parts: list[str] = []
    for md_file in markdown_files:
        content = md_file.read_text(encoding="utf-8").strip()
        if not content:
            continue
        parts.append(content)

    if not parts:
        raise ValueError(f"Markdown output files are empty in: {extract_dir}")

    return "\n\n".join(parts)


class MineruExtractor:
    def __init__(self):
        self.api_url = os.environ.get("MINERU_API_URL")
        self.image_extractor = ImageExtractor()

    async def extract(
        self,
        project: str,
        source: Path,
        languages: list[str] = const.DEFAULT_LANGUAGES,
    ) -> str:
        logger.info(f"Extracting text with MinerU for file: {source}")
        source_path = source.expanduser().resolve()
        if not source_path.is_file():
            raise FileNotFoundError(f"Input file does not exist: {source_path}")

        upload_assets = [
            api_client.UploadAsset(path=source_path, upload_name=source_path.name)
        ]

        result_zip_path: Path | None = None

        async with httpx.AsyncClient(
            timeout=api_client.build_http_timeout(),
            follow_redirects=True,
        ) as http_client:
            try:
                server_health = await api_client.fetch_server_health(
                    http_client,
                    api_client.normalize_base_url(self.api_url),
                )

                submit_response = await api_client.submit_parse_task(
                    base_url=server_health.base_url,
                    upload_assets=upload_assets,
                    form_data=_build_form_data(languages=languages),
                )

                await api_client.wait_for_task_result(
                    client=http_client,
                    submit_response=submit_response,
                    task_label=source_path.name,
                )
                result_zip_path = await api_client.download_result_zip(
                    client=http_client,
                    submit_response=submit_response,
                    task_label=source_path.name,
                )
            except Exception as e:
                logger.error(f"Error extracting text with MinerU: {e}")
                raise e

        try:
            with tempfile.TemporaryDirectory(prefix="mineru-extract-") as tmp_dir:
                extract_dir = Path(tmp_dir)
                api_client.safe_extract_zip(result_zip_path, extract_dir)
                return await self._parse_images(project, source, extract_dir)
        finally:
            result_zip_path.unlink(missing_ok=True)

    async def _parse_images(self, project: str, source: Path, extract_dir: Path) -> str:
        text = _read_extracted_markdown(extract_dir)
        image_id = 1
        for image_file in sorted(extract_dir.rglob("*.jpg")):
            # replace ![...](... file_name ...) with <Image .../> tag
            pattern = re.compile(
                r"!\[[^\]]*\]\([^)]*" + re.escape(image_file.stem) + r"[^)]*\)"
            )
            if not pattern.search(text):
                continue

            image_meta = await self.image_extractor.extract(image_file)
            if image_meta.info_loss:
                image_tag = f"<Image>\n  <ID>{image_id}</ID>\n  <Content>{image_meta.content}</Content>\n</Image>"
                # move the file to image store
                image_path = Path(
                    f"store/s3/images/{project}/{source.name}/{image_id}.jpg"
                )
                image_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(image_file, image_path)
                image_id += 1
            else:
                image_tag = image_meta.content
            text = pattern.sub(image_tag + "\n", text)

        return text
