"""Extraction requests queued by the data management UI.

A row means "run ``extract_document`` for this raw file". ``status`` is
``pending`` until the run fails (``failed``). A run that reaches review
deletes the row, because the review row then carries the file's state.
"""

from typing import Any

from common.db import DBClient

_db = DBClient()

_CREATE_EXTRACTION_REQUEST_TABLE = """
CREATE TABLE IF NOT EXISTS extraction_request (
    project TEXT NOT NULL,
    filename TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    request_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    error TEXT,
    PRIMARY KEY (project, filename)
)
"""

_SELECT_PENDING = """
SELECT project, filename, request_time
FROM extraction_request
WHERE status = 'pending'
ORDER BY request_time
"""

_MARK_FAILED = """
UPDATE extraction_request
SET status = 'failed',
    error = %s
WHERE project = %s AND filename = %s AND status = 'pending'
"""

_DELETE_REQUEST = """
DELETE FROM extraction_request
WHERE project = %s AND filename = %s
"""


def _ensure_table() -> None:
    _db.execute(_CREATE_EXTRACTION_REQUEST_TABLE)


def list_pending_requests() -> list[dict[str, Any]]:
    """Requests that have not failed and have not reached review."""
    _ensure_table()
    return _db.fetchall(_SELECT_PENDING)


def mark_request_failed(project: str, filename: str, error: str | None) -> None:
    _ensure_table()
    _db.execute(_MARK_FAILED, (error, project, filename))


def delete_extraction_request(project: str, filename: str) -> None:
    _ensure_table()
    _db.execute(_DELETE_REQUEST, (project, filename))
