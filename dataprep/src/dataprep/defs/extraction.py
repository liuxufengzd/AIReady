"""Extraction group: one raw file becomes a review-ready document.

``raw_directory_sensor`` watches ``{store}/raw/{project}/`` and launches
``extract_document``. Assets in the ``extraction`` group share
``document_partitions`` and run in that job, in order:

``prepared_document`` -> ``mineru_artifact`` -> ``layout`` -> ``cleaned_document``

Work before review stays in ``processed/{project}/{filename}/tmp/``. Each asset
passes the parse-source path to the next one through ``MaterializeResult.value``.
After review, that directory is copied onto ``processed/{project}/{filename}/``
and ``tmp`` is removed.
"""

import shutil
from pathlib import Path

import dagster as dg

from dataprep.common.const import SUPPORTED_FILE_TYPES
from dataprep.office_converter import is_office_document, office_to_pdf
from dataprep.defs.resources import DocumentStore
from dataprep.parsing import (
    MARKDOWN_NAME,
    extract_images,
    parse_with_mineru,
    write_layout,
)

_PARTITION_SEPARATOR = "/"
_RETRY = dg.RetryPolicy(max_retries=2)

document_partitions = dg.DynamicPartitionsDefinition(name="raw_documents")


def _document_key(project: str, filename: str) -> str:
    if _PARTITION_SEPARATOR in project or _PARTITION_SEPARATOR in filename:
        raise ValueError(f"Invalid document identity: {project!r} / {filename!r}")
    return f"{project}{_PARTITION_SEPARATOR}{filename}"


def _parse_document_key(key: str) -> tuple[str, str]:
    project, separator, filename = key.partition(_PARTITION_SEPARATOR)
    if not separator or not project or not filename or _PARTITION_SEPARATOR in filename:
        raise ValueError(f"Invalid document partition key: {key!r}")
    return project, filename


def _publish_parse_source(source: Path, output_dir: Path) -> Path:
    """Copy a PDF or image, or convert an Office file, into ``output_dir``.
    Return the published file path."""
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    if is_office_document(source):
        published = office_to_pdf(source, output_dir)
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
    context: dg.AssetExecutionContext, store: DocumentStore
) -> dg.MaterializeResult:
    project, filename = _parse_document_key(context.partition_key)
    source = store.raw_file(project, filename)
    if not source.is_file():
        raise FileNotFoundError(f"Raw file not found: {source}")
    if source.suffix.lower() not in SUPPORTED_FILE_TYPES:
        raise ValueError(f"Unsupported file type: {source.suffix}")

    published = _publish_parse_source(source, store.working_dir(project, filename))
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
    context: dg.AssetExecutionContext, prepared_document: str
) -> dg.MaterializeResult:
    source = Path(prepared_document)
    await parse_with_mineru(source, log=context.log.info)
    context.log.info("Parsed %s", source.name)
    return dg.MaterializeResult(value=prepared_document)


@dg.asset(
    partitions_def=document_partitions,
    group_name="extraction",
    description="Draw MinerU regions onto the parse source. A failure leaves the parse in place.",
)
def layout(
    context: dg.AssetExecutionContext, mineru_artifact: str
) -> dg.MaterializeResult:
    source = Path(mineru_artifact)
    written = write_layout(source, log=context.log.warning)
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
    context: dg.AssetExecutionContext, layout: str
) -> dg.MaterializeResult:
    source = Path(layout)
    retained = await extract_images(source)
    markdown = source.parent / MARKDOWN_NAME
    context.log.info("Retained %s image(s)", retained)
    return dg.MaterializeResult(
        value=str(markdown),
        metadata={"retained_images": retained},
    )


extract_document = dg.define_asset_job(
    name="extract_document",
    selection=dg.AssetSelection.groups("extraction"),
    description="Prepare, parse, draw layout, and extract images up to human review.",
)


@dg.sensor(
    job=extract_document,
    minimum_interval_seconds=10,
    default_status=dg.DefaultSensorStatus.RUNNING,
    description="Start extract_document when a supported file appears under a project's raw directory.",
)
def raw_directory_sensor(
    context: dg.SensorEvaluationContext, store: DocumentStore
) -> dg.SensorResult | dg.SkipReason:
    last_mtime = int(context.cursor) if context.cursor else 0
    max_mtime = last_mtime
    partition_keys: list[str] = []
    run_requests: list[dg.RunRequest] = []

    for project, filename, mtime_ns in store.iter_raw_files():
        if mtime_ns <= last_mtime:
            continue

        key = _document_key(project, filename)
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
