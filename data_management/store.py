"""Paths on the shared document store, ``{repo}/store/s3``.

``raw/{project}/{filename}`` is the landing file. ``processed/{project}/{filename}/``
holds the extraction output, with work before review in its ``tmp`` directory.
"""

from pathlib import Path

from common.published import store_root

RAW_DIRNAME = "raw"
PROCESSED_DIRNAME = "processed"
LAYOUT_NAME = "layout.pdf"


def component(value: str, label: str) -> str:
    """Reject names that would leave the document store."""
    if not value or value in {".", ".."} or value != Path(value).name:
        raise ValueError(f"Invalid {label}: {value!r}")
    return value


def under(*parts: str) -> Path:
    base = store_root().resolve()
    target = base.joinpath(*parts).resolve()
    if not target.is_relative_to(base):
        raise ValueError(f"Path escapes the document store: {target}")
    return target


def raw_root() -> Path:
    return under(RAW_DIRNAME)


def raw_file(project: str, filename: str) -> Path:
    return under(
        RAW_DIRNAME, component(project, "project"), component(filename, "filename")
    )


def processed_dir(project: str, filename: str) -> Path:
    return under(
        PROCESSED_DIRNAME,
        component(project, "project"),
        component(filename, "filename"),
    )


def content_dir(project: str, filename: str) -> Path:
    """Directory that currently holds the parse: tmp, or the published folder.

    This matches the directory publish reads when it chunks an approved file.
    """
    published = processed_dir(project, filename)
    working = published / "tmp"
    if working.is_dir() and any(working.iterdir()):
        return working
    if published.is_dir():
        return published
    raise FileNotFoundError(f"No extraction output for {project}/{filename}")


def asset_under(directory: Path, relative: str) -> Path:
    """A file inside ``directory``, such as an image the markdown references."""
    if not relative or relative.startswith(("/", "\\")):
        raise FileNotFoundError(relative)
    root = directory.resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise FileNotFoundError(relative)
    return target
