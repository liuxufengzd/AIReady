"""Review table access for documents waiting on human review."""

from datetime import datetime
from typing import Any

from dataprep.resources.dbclient import DBClient


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

_UPSERT_REVIEW = """
INSERT INTO review (
    project, filename, token_num, engine, create_time, approved, require_chunking, processed
)
VALUES (%s, %s, %s, %s, NOW(), NULL, FALSE, FALSE)
ON CONFLICT (project, filename, engine) DO UPDATE
SET token_num = %s,
    create_time = NOW(),
    approved = NULL,
    require_chunking = FALSE,
    processed = FALSE
"""

_SELECT_READY = """
SELECT project, filename, engine, create_time
FROM review
WHERE processed = FALSE AND approved IS NOT NULL
ORDER BY create_time
"""

_SELECT_BY_ENGINE = """
SELECT project, filename, token_num, engine, approved, require_chunking, processed, create_time
FROM review
WHERE project = %s AND filename = %s AND engine = %s
"""

_MARK_PROCESSED = """
UPDATE review
SET processed = TRUE
WHERE project = %s AND filename = %s AND engine = %s
  AND create_time = %s
  AND approved IS NOT NULL
  AND processed = FALSE
"""


def _ensure_review_table(db: DBClient) -> None:
    """Create the review table when this database has never stored a review."""
    db.execute(_CREATE_REVIEW_TABLE)


def save_review(
    db: DBClient, project: str, filename: str, token_num: int, engine: str
) -> None:
    """Record one extraction as waiting for review.

    A later run of the same document replaces the row and clears any earlier
    approval, because the extracted content has been produced again.
    """
    _ensure_review_table(db)
    db.execute(_UPSERT_REVIEW, (project, filename, token_num, engine, token_num))


def list_ready_reviews(db: DBClient) -> list[dict[str, Any]]:
    """Rows a human has decided and whose files are not yet published."""
    _ensure_review_table(db)
    return db.fetchall(_SELECT_READY)


def get_review(
    db: DBClient, project: str, filename: str, engine: str
) -> dict[str, Any] | None:
    """Load one review row."""
    _ensure_review_table(db)
    return db.fetchone(_SELECT_BY_ENGINE, (project, filename, engine))


def mark_review_processed(
    db: DBClient,
    project: str,
    filename: str,
    engine: str,
    create_time: datetime,
) -> int:
    """Mark one decided row processed. Return how many rows changed.

    The ``create_time`` guard ignores a row that extraction has replaced
    while publish was running.
    """
    _ensure_review_table(db)
    return db.execute_rowcount(
        _MARK_PROCESSED, (project, filename, engine, create_time)
    )
