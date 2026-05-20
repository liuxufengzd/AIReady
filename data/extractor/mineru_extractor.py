import os
import tempfile
from pathlib import Path

import httpx
from mineru.cli import api_client

from common.logger import get_logger
import re

logger = get_logger(__name__)


def _prepare_local_api_temp_dir() -> None:
    current_temp_dir = Path(tempfile.gettempdir())
    if os.name == "nt" or not Path("/tmp").exists():
        return
    if not str(current_temp_dir).startswith("/mnt/"):
        return

    # vLLM/ZeroMQ IPC sockets fail on drvfs-backed temp directories under WSL.
    os.environ["TMPDIR"] = "/tmp"
    tempfile.tempdir = None


def _build_form_data(
    languages: list[str],
    backend: str,
    parse_method: str,
    formula_enable: bool,
    table_enable: bool,
    image_analysis: bool,
    server_url: str | None,
    start_page_id: int,
    end_page_id: int | None,
) -> dict[str, str | list[str]]:
    return api_client.build_parse_request_form_data(
        lang_list=languages,
        backend=backend,
        parse_method=parse_method,
        formula_enable=formula_enable,
        table_enable=table_enable,
        image_analysis=image_analysis,
        server_url=server_url,
        start_page_id=start_page_id,
        end_page_id=end_page_id,
        return_md=True,
        return_middle_json=False,
        return_model_output=False,
        return_content_list=False,
        return_images=False,
        response_format_zip=True,
        return_original_file=False,
    )


def _read_extracted_markdown(extract_dir: Path) -> str:
    markdown_files = sorted(extract_dir.rglob("*.md"))
    if not markdown_files:
        raise ValueError(f"No markdown output found in extracted result: {extract_dir}")

    parts: list[str] = []
    for md_file in markdown_files:
        content = md_file.read_text(encoding="utf-8")
        if not content.strip():
            continue
        parts.append(content)

    if not parts:
        raise ValueError(f"Markdown output files are empty in: {extract_dir}")

    res = "\n\n".join(parts)
    res = re.sub(r"!\[.*?\]\(images/.*?\)", "", res)
    return res.strip()


class MineruExtractor:
    async def extract(
        self,
        source: Path,
        *,
        languages: list[str] = ["en", "ja"],
        backend: str = "hybrid-auto-engine",
        parse_method: str = "auto",
        formula_enable: bool = True,
        table_enable: bool = True,
        image_analysis: bool = True,
        api_url: str | None = None,
        server_url: str | None = None,
        start_page_id: int = 0,
        end_page_id: int | None = None,
    ) -> str:
        logger.info(f"Extracting text with MinerU for file: {source}")
        source_path = source.expanduser().resolve()
        if not source_path.is_file():
            raise FileNotFoundError(f"Input file does not exist: {source_path}")

        if backend.endswith("http-client") and not server_url:
            raise ValueError(f"backend={backend} requires server_url")

        form_data = _build_form_data(
            languages=languages,
            backend=backend,
            parse_method=parse_method,
            formula_enable=formula_enable,
            table_enable=table_enable,
            image_analysis=image_analysis,
            server_url=server_url,
            start_page_id=start_page_id,
            end_page_id=end_page_id,
        )
        upload_assets = [
            api_client.UploadAsset(path=source_path, upload_name=source_path.name)
        ]

        local_server: api_client.LocalAPIServer | None = None
        result_zip_path: Path | None = None

        async with httpx.AsyncClient(
            timeout=api_client.build_http_timeout(),
            follow_redirects=True,
        ) as http_client:
            try:
                if api_url:
                    server_health = await api_client.fetch_server_health(
                        http_client,
                        api_client.normalize_base_url(api_url),
                    )
                else:
                    _prepare_local_api_temp_dir()
                    local_server = api_client.LocalAPIServer()
                    base_url = local_server.start()
                    logger.info(f"Started local mineru-api: {base_url}")
                    server_health = await api_client.wait_for_local_api_ready(
                        http_client,
                        local_server,
                    )

                submit_response = await api_client.submit_parse_task(
                    base_url=server_health.base_url,
                    upload_assets=upload_assets,
                    form_data=form_data,
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
            finally:
                if local_server is not None:
                    local_server.stop()

        try:
            with tempfile.TemporaryDirectory(prefix="mineru-extract-") as tmp_dir:
                extract_dir = Path(tmp_dir)
                api_client.safe_extract_zip(result_zip_path, extract_dir)
                return _read_extracted_markdown(extract_dir)
        finally:
            result_zip_path.unlink(missing_ok=True)
