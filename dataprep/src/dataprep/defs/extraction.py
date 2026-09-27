"""Extraction group: one raw file becomes a review-ready document.

``raw_directory_sensor`` watches ``{store}/raw/{project}/`` and launches
``extract_document``. Assets in the ``extraction`` group share
``document_partitions`` and run in that job, in order:

``prepared_document`` -> ``mineru_artifact`` -> ``layout`` -> ``document_to_review``

Work before review stays in ``processed/{project}/{filename}/tmp/``. Each asset
passes the parse-source path to the next one through ``MaterializeResult.value``.
After a human sets ``approved``, ``reviewed_document_sensor`` starts
``publish_document``, which copies the accepted output onto
``processed/{project}/{filename}/`` and removes ``tmp``.
"""

import shutil
from pathlib import Path

import dagster as dg

from dataprep.common.const import IMAGES_DIRNAME, MARKDOWN_NAME, SUPPORTED_FILE_TYPES
from dataprep.common.utils import document_key, document_partitions, parse_document_key
from dataprep.dao.review import save_review
from dataprep.resources.dbclient import DBClient
from dataprep.resources.llm import LLM
from dataprep.resources.image_extractor import ImageExtractor
from dataprep.resources.local_doc_store import LocalDocStore
from dataprep.resources.mineru_parser import MinerUParser
from dataprep.resources.office_converter import OfficeConverter

_RETRY = dg.RetryPolicy(max_retries=2)
# S3 LastModified resolves to one second. The cursor is nanoseconds.
_WATERMARK_LAG_NS = 1_000_000_000


def _publish_parse_source(
    source: Path, output_dir: Path, office_converter: OfficeConverter
) -> Path:
    """Copy a PDF or image, or convert an Office file, into ``output_dir``.
    Return the published file path."""
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    if office_converter.is_office_document(source):
        published = office_converter.convert(source, output_dir)
        # LibreOffice keeps a private profile beside the PDF.
        shutil.rmtree(output_dir / "lo-profile", ignore_errors=True)
        return published
    published = output_dir / source.name
    shutil.copy2(source, published)
    return published


@dg.asset(
    partitions_def=document_partitions,
    group_name="extraction",
    retry_policy=_RETRY,
    description=(
        "Turn one raw file into the PDF or image MinerU will parse. "
        "Office files are converted with LibreOffice; PDF and image files are copied."
    ),
)
def prepared_document(
    context: dg.AssetExecutionContext,
    store: LocalDocStore,
    office_converter: dg.ResourceParam[OfficeConverter],
) -> dg.MaterializeResult:
    project, filename = parse_document_key(context.partition_key)
    source = store.raw_file(project, filename)
    if not source.is_file():
        raise FileNotFoundError(f"Raw file not found: {source}")
    if source.suffix.lower() not in SUPPORTED_FILE_TYPES:
        raise ValueError(f"Unsupported file type: {source.suffix}")

    published = _publish_parse_source(
        source, store.working_dir(project, filename), office_converter
    )
    context.log.info("Prepared %s -> %s", filename, published.name)
    return dg.MaterializeResult(
        value=str(published),
        metadata={
            "project": project,
            "raw_name": filename,
        },
    )


@dg.asset(
    partitions_def=document_partitions,
    group_name="extraction",
    retry_policy=_RETRY,
    description="Parse the prepared PDF or image with MinerU.",
)
async def mineru_artifact(
    context: dg.AssetExecutionContext,
    prepared_document: str,
    mineru_parser: MinerUParser,
) -> dg.MaterializeResult:
    source = Path(prepared_document)
    await mineru_parser.parse_async(source, logger=context.log.info)

    context.log.info("Parsed %s", source.name)
    return dg.MaterializeResult(value=prepared_document)


@dg.asset(
    partitions_def=document_partitions,
    group_name="extraction",
    description="Draw MinerU regions onto the parse source. A failure leaves the parse in place.",
)
def layout(
    context: dg.AssetExecutionContext,
    mineru_artifact: str,
    mineru_parser: MinerUParser,
) -> dg.MaterializeResult:
    source = Path(mineru_artifact)
    written = mineru_parser.write_layout(source, logger=context.log.warning)

    context.log.info("Layout written successfully")
    return dg.MaterializeResult(
        value=mineru_artifact,
        metadata={"layout_written": written},
    )


@dg.asset(
    partitions_def=document_partitions,
    group_name="extraction",
    retry_policy=_RETRY,
    description="Inline image text into the markdown and keep images that cannot be fully described.",
)
async def document_to_review(
    context: dg.AssetExecutionContext,
    layout: str,
    image_extractor: ImageExtractor,
    db: DBClient,
    llm: LLM,
) -> dg.MaterializeResult:
    source = Path(layout)
    markdown = source.parent / MARKDOWN_NAME
    images_store = source.parent / IMAGES_DIRNAME
    text = await image_extractor.extract_images_from_doc(markdown, images_store)

    # Clean up intermediate JSON files
    for path in source.parent.rglob("*.json"):
        path.unlink(missing_ok=True)

    project, filename = parse_document_key(context.partition_key)
    token_num = llm.client.get_num_tokens(text)
    save_review(db, project, filename, token_num, "mineru")

    context.log.info("Images extracted successfully, waiting for human review")
    return dg.MaterializeResult(value=str(markdown))


extract_document = dg.define_asset_job(
    name="extract_document",
    selection=dg.AssetSelection.groups("extraction"),
    description="Prepare, parse, draw layout, and extract images up to human review.",
)


@dg.sensor(
    job=extract_document,
    minimum_interval_seconds=30,
    default_status=dg.DefaultSensorStatus.RUNNING,
    description="Start extract_document when a supported file appears under a project's raw directory.",
)
def raw_directory_sensor(
    context: dg.SensorEvaluationContext, store: LocalDocStore
) -> dg.SensorResult | dg.SkipReason:
    last_mtime = int(context.cursor) if context.cursor else 0
    max_mtime = last_mtime
    partition_keys: list[str] = []
    run_requests: list[dg.RunRequest] = []

    for project, filename, mtime_ns in store.iter_raw_files():
        # One second behind the watermark. A repeat from that second has the same
        # run_key, so Dagster does not start another run.
        if mtime_ns <= last_mtime - _WATERMARK_LAG_NS:
            continue

        key = document_key(project, filename)
        partition_keys.append(key)
        run_requests.append(
            dg.RunRequest(partition_key=key, run_key=f"{key}:{mtime_ns}")
        )
        max_mtime = max(max_mtime, mtime_ns)

    context.update_cursor(str(max_mtime))
    if not run_requests:
        return dg.SkipReason("No new files in project raw directories.")

    # Maximum of 25K partitions per sensor evaluation, as this is the maximum recommended partition limit per asset
    return dg.SensorResult(
        run_requests=run_requests,
        dynamic_partitions_requests=[
            document_partitions.build_add_request(partition_keys)
        ],  # Register the required partitions to the dagster
    )
