import shutil
from pathlib import Path

from data.common.office_converter import is_office_document

_MARKDOWN_NAME = "markdown.md"
_LAYOUT_NAME = "layout.pdf"


def review_artifact_dir(project: str, filename: str) -> Path:
    """Directory holding one document's parse while it is under review.

    Lives at ``store/tmp/{project}/{filename}/``. An Office file is converted
    to one PDF before parsing, so it uses this same directory.
    """
    return Path("store/tmp") / project / filename


def processed_artifact_dir(project: str, filename: str) -> Path:
    """Accepted parse output: ``store/s3/processed/{project}/{filename}/``."""
    return Path("store/s3/processed") / project / filename


def raw_retrieval_path(project: str, filename: str) -> Path:
    """File returned when a chunk asks for the original document.

    An Office file is stored as the PDF that MinerU parsed, and search reads that PDF.
    """
    root = Path("store/s3/processed") / project
    if is_office_document(Path(filename)):
        return root / f"{filename}.pdf"
    return root / filename


def markdown_path(project: str, filename: str) -> Path:
    return review_artifact_dir(project, filename) / _MARKDOWN_NAME


def layout_path(project: str, filename: str) -> Path:
    return review_artifact_dir(project, filename) / _LAYOUT_NAME


def discard_review_artifacts(project: str, filename: str) -> None:
    """Delete a rejected parse."""
    directory = review_artifact_dir(project, filename)
    if directory.exists():
        shutil.rmtree(directory)


def promote_review_artifacts(project: str, filename: str) -> bool:
    """Copy a review directory into processed storage and delete the temporary one.

    Returns False when the review directory is already gone, which is the case
    after a rejected parse.
    """
    source = review_artifact_dir(project, filename)
    if not source.exists():
        return False
    if not any(source.rglob("*")):
        shutil.rmtree(source)
        return False

    destination = processed_artifact_dir(project, filename)
    if destination.exists():
        if destination.is_dir():
            shutil.rmtree(destination)
        else:
            destination.unlink()
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)
    shutil.rmtree(source)
    return True
