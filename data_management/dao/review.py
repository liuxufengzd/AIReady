"""Review rows this service lists and decides."""

from datetime import datetime
from typing import Any

from common.db import DBClient

_db = DBClient()


def close() -> None:
    """Close this service's review connection pool."""
    _db.close()


_CREATE_REVIEW_TABLE = """
CREATE TABLE IF NOT EXISTS review (
    project TEXT NOT NULL,
    filename TEXT NOT NULL,
    token_num INT NOT NULL,
    engine TEXT NOT NULL,
    create_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved BOOLEAN,
    require_chunking BOOLEAN NOT NULL DEFAULT FALSE,
    processed BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (project, filename, engine)
)
"""

_SELECT_ALL = """
SELECT project, filename, token_num, engine, approved, require_chunking, processed, create_time
FROM review
ORDER BY (approved IS NULL) DESC, create_time DESC, project, filename
"""

_SELECT_BY_ENGINE = """
SELECT project, filename, token_num, engine, approved, require_chunking, processed, create_time
FROM review
WHERE project = %s AND filename = %s AND engine = %s
"""

_RECORD_DECISION = """
UPDATE review
SET token_num = %s,
    approved = %s,
    require_chunking = %s
WHERE project = %s AND filename = %s AND engine = %s
  AND create_time = %s
  AND approved IS NULL
"""


def _ensure_review_table() -> None:
    _db.execute(_CREATE_REVIEW_TABLE)


def list_reviews() -> list[dict[str, Any]]:
    """Every review row. Undecided rows come first."""
    _ensure_review_table()
    return _db.fetchall(_SELECT_ALL)


def get_review(project: str, filename: str, engine: str) -> dict[str, Any] | None:
    """Load one review row."""
    _ensure_review_table()
    return _db.fetchone(_SELECT_BY_ENGINE, (project, filename, engine))


def record_review_decision(
    project: str,
    filename: str,
    engine: str,
    token_num: int,
    approved: bool,
    require_chunking: bool,
    create_time: datetime,
) -> int:
    """Store the human decision. Return how many rows changed.

    ``token_num`` is the count after review, because the reviewer may have
    edited the markdown. A row that already has a decision, or that extraction
    has replaced, is left unchanged.
    """
    _ensure_review_table()
    return _db.execute_rowcount(
        _RECORD_DECISION,
        (
            token_num,
            approved,
            require_chunking,
            project,
            filename,
            engine,
            create_time,
        ),
    )
