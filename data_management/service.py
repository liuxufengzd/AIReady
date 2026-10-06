"""List review-table files and record a human decision on one of them."""

from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from common.const import MARKDOWN_NAME, MUST_CHUNK_TOKEN_THRESHOLD
from common.logger import get_logger
from data_management.dao.review import (
    get_review,
    list_reviews,
    record_review_decision,
)
from data_management.paging import PAGE_SIZE, paginate
from data_management.store import LAYOUT_NAME, asset_under, component, content_dir
from common.util import get_llm

logger = get_logger(__name__)


load_dotenv(Path(__file__).parent / ".env")


class Conflict(Exception):
    """The file's current state does not allow the operation."""


class TokenCountError(Exception):
    """The text could not be counted."""


_llm = None


def count_tokens(text: str) -> int:
    """Token count of ``text`` under the configured model."""
    global _llm
    if _llm is None:
        _llm = get_llm()
    return int(_llm.get_num_tokens(text))


class ReviewService:
    """Review-table queries and the markdown those rows point at."""

    def list_files(
        self,
        project: str | None = None,
        pending: bool | None = None,
        query: str = "",
        page: int = 1,
        page_size: int = PAGE_SIZE,
    ) -> dict:
        """One page of review rows, undecided first, then newest first.

        ``pending`` keeps only undecided rows (True) or decided ones (False).
        ``query`` matches the file name or engine.
        """
        rows = list_reviews(project)
        if pending is not None:
            rows = [row for row in rows if (row["approved"] is None) == pending]
        needle = query.strip().lower()
        if needle:
            rows = [
                row
                for row in rows
                if needle in row["filename"].lower() or needle in row["engine"].lower()
            ]
        rows, paging = paginate(rows, page, page_size)
        return {"rows": [_public_row(row) for row in rows], **paging}

    def open_review(self, project: str, filename: str, engine: str) -> dict:
        """Load one undecided row and the markdown waiting beside it."""
        project, filename, engine = _identity(project, filename, engine)
        row = self._pending_row(project, filename, engine)
        directory = content_dir(project, filename)
        markdown = directory / MARKDOWN_NAME
        if not markdown.is_file():
            raise FileNotFoundError(
                f"Extracted markdown not found for {project}/{filename}"
            )
        token_num = int(row["token_num"])
        return {
            **_public_row(row),
            "content": markdown.read_text(encoding="utf-8"),
            "has_layout": (directory / LAYOUT_NAME).is_file(),
            "ask_chunking": token_num <= MUST_CHUNK_TOKEN_THRESHOLD,
            "permit_reject": True,
        }

    def layout_file(self, project: str, filename: str, engine: str) -> Path:
        project, filename, engine = _identity(project, filename, engine)
        path = content_dir(project, filename) / LAYOUT_NAME
        if not path.is_file():
            raise FileNotFoundError(f"Layout PDF not found for {project}/{filename}")
        return path

    def asset_file(
        self, project: str, filename: str, engine: str, relative: str
    ) -> Path:
        """A file next to the markdown, such as an extracted image."""
        project, filename, engine = _identity(project, filename, engine)
        return asset_under(content_dir(project, filename), relative)

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
            token_num = _count_reviewed_tokens(text)
            chunking = bool(require_chunking) or token_num > MUST_CHUNK_TOKEN_THRESHOLD
        else:
            token_num = -1
            chunking = False
            text = None

        if text is not None:
            markdown = content_dir(project, filename) / MARKDOWN_NAME
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
            raise Conflict(
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
            raise Conflict(f"{project}/{filename} has already been reviewed.")
        if create_time is not None and row["create_time"] != create_time:
            raise Conflict(
                f"{project}/{filename} was extracted again. Open the latest copy."
            )
        return row



def _count_reviewed_tokens(text: str) -> int:
    try:
        return count_tokens(text)
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
        component(project, "project"),
        component(filename, "filename"),
        component(engine, "engine"),
    )
