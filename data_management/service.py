"""List review-table files and record a human decision on one of them.
We can add file CRUD operations in the future.
"""

from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from common.const import MARKDOWN_NAME, MUST_CHUNK_TOKEN_THRESHOLD
from common.logger import get_logger
from common.review import (
    close as close_db,
    get_review,
    list_reviews,
    record_review_decision,
)
from common.util import get_llm

logger = get_logger(__name__)

_LAYOUT_NAME = "layout.pdf"
_REPO_ROOT = Path(__file__).resolve().parent.parent


load_dotenv(Path(__file__).parent / ".env")


class ReviewConflict(Exception):
    """The row can no longer take a decision."""


class TokenCountError(Exception):
    """The edited markdown could not be counted."""


class ReviewService:
    """Review-table queries and the markdown those rows point at."""

    def __init__(self) -> None:
        self._llm = None

    def close(self) -> None:
        close_db()

    def list_files(self) -> list[dict]:
        return list_reviews()

    def open_review(self, project: str, filename: str, engine: str) -> dict:
        """Load one undecided row and the markdown waiting beside it."""
        project, filename, engine = _identity(project, filename, engine)
        row = self._pending_row(project, filename, engine)
        directory = _content_dir(project, filename)
        markdown = directory / MARKDOWN_NAME
        if not markdown.is_file():
            raise FileNotFoundError(
                f"Extracted markdown not found for {project}/{filename}"
            )
        token_num = int(row["token_num"])
        return {
            **_public_row(row),
            "content": markdown.read_text(encoding="utf-8"),
            "has_layout": (directory / _LAYOUT_NAME).is_file(),
            "ask_chunking": token_num <= MUST_CHUNK_TOKEN_THRESHOLD,
            "permit_reject": True,
        }

    def layout_file(self, project: str, filename: str, engine: str) -> Path:
        project, filename, engine = _identity(project, filename, engine)
        path = _content_dir(project, filename) / _LAYOUT_NAME
        if not path.is_file():
            raise FileNotFoundError(f"Layout PDF not found for {project}/{filename}")
        return path

    def asset_file(
        self, project: str, filename: str, engine: str, relative: str
    ) -> Path:
        """A file next to the markdown, such as an extracted image."""
        project, filename, engine = _identity(project, filename, engine)
        return _asset_under(_content_dir(project, filename), relative)

    def submit_review(
        self,
        project: str,
        filename: str,
        engine: str,
        approved: bool,
        text: str | None,
        require_chunking: bool,
        create_time: datetime,
    ) -> dict:
        """Save an approval's edited markdown and write the review decision.

        Rejection leaves the extracted file untouched. Approval replaces
        ``markdown.md`` with the text the reviewer submitted, then stores the
        new token count.
        """
        project, filename, engine = _identity(project, filename, engine)
        row = self._pending_row(project, filename, engine, create_time)
        if approved:
            if text is None or not text.strip():
                raise ValueError(
                    "Cannot approve empty content. Add text or reject the parse."
                )
            token_num = self._count_tokens(text)
            chunking = bool(require_chunking) or token_num > MUST_CHUNK_TOKEN_THRESHOLD
        else:
            token_num = -1
            chunking = False
            text = None

        if text is not None:
            markdown = _content_dir(project, filename) / MARKDOWN_NAME
            markdown.write_text(text, encoding="utf-8")

        updated = record_review_decision(
            project,
            filename,
            engine,
            token_num,
            approved,
            chunking,
            row["create_time"],
        )
        if updated != 1:
            raise ReviewConflict(
                f"{project}/{filename} changed while it was being reviewed. Open it again."
            )

        logger.info(
            "Recorded review %s/%s engine=%s approved=%s tokens=%s chunking=%s",
            project,
            filename,
            engine,
            approved,
            token_num,
            chunking,
        )
        saved = get_review(project, filename, engine)
        if saved is None:
            raise FileNotFoundError(
                f"No review row for {project}/{filename} ({engine})"
            )
        return _public_row(saved)

    def _pending_row(
        self,
        project: str,
        filename: str,
        engine: str,
        create_time: datetime | None = None,
    ) -> dict:
        row = get_review(project, filename, engine)
        if row is None:
            raise FileNotFoundError(
                f"No review row for {project}/{filename} ({engine})"
            )
        if row["approved"] is not None:
            raise ReviewConflict(f"{project}/{filename} has already been reviewed.")
        if create_time is not None and row["create_time"] != create_time:
            raise ReviewConflict(
                f"{project}/{filename} was extracted again. Open the latest copy."
            )
        return row

    def _count_tokens(self, text: str) -> int:
        try:
            if self._llm is None:
                self._llm = get_llm()
            return int(self._llm.get_num_tokens(text))
        except Exception as exc:
            logger.exception("Token count failed")
            raise TokenCountError(
                "Could not count tokens for the reviewed markdown."
            ) from exc


def _public_row(row: dict) -> dict:
    return {
        "project": row["project"],
        "filename": row["filename"],
        "engine": row["engine"],
        "token_num": int(row["token_num"]),
        "approved": row["approved"],
        "require_chunking": bool(row["require_chunking"]),
        "processed": bool(row["processed"]),
        "create_time": row["create_time"],
    }


def _identity(project: str, filename: str, engine: str) -> tuple[str, str, str]:
    return (
        _component(project, "project"),
        _component(filename, "filename"),
        _component(engine, "engine"),
    )


def _component(value: str, label: str) -> str:
    """Reject names that would leave the document store."""
    if not value or value in {".", ".."} or value != Path(value).name:
        raise ValueError(f"Invalid {label}: {value!r}")
    return value


def _content_dir(project: str, filename: str) -> Path:
    """Directory that currently holds the parse: tmp, or the published folder.

    This matches the directory publish reads when it chunks an approved file.
    """
    published = _under("processed", project, filename)
    working = published / "tmp"
    if working.is_dir() and any(working.iterdir()):
        return working
    if published.is_dir():
        return published
    raise FileNotFoundError(f"No extraction output for {project}/{filename}")


def _asset_under(directory: Path, relative: str) -> Path:
    if not relative or relative.startswith(("/", "\\")):
        raise FileNotFoundError(relative)
    root = directory.resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise FileNotFoundError(relative)
    return target


def _under(*parts: str) -> Path:
    base = (_REPO_ROOT / "store" / "s3").resolve()
    target = base.joinpath(*parts).resolve()
    if not target.is_relative_to(base):
        raise ValueError(f"Path escapes the document store: {target}")
    return target
