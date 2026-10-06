"""Extraction requests this service queues for dataprep.

dataprep's ``extraction_request_sensor`` launches a run for each ``pending``
row. A failed run sets ``failed``; a run that reaches review deletes the row.
"""

from typing import Any

from data_management.dao import db as _db

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

# A pending request is left alone, so its run is not launched twice.
_QUEUE_REQUEST = """
INSERT INTO extraction_request (project, filename, status, request_time, error)
VALUES (%s, %s, 'pending', NOW(), NULL)
ON CONFLICT (project, filename) DO UPDATE
SET status = 'pending',
    request_time = NOW(),
    error = NULL
WHERE extraction_request.status <> 'pending'
"""

_SELECT_BY_PROJECT = """
SELECT project, filename, status, request_time, error
FROM extraction_request
WHERE project = %s
"""

_DELETE_REQUEST = """
DELETE FROM extraction_request
WHERE project = %s AND filename = %s
"""


def _ensure_table() -> None:
    _db.execute(_CREATE_EXTRACTION_REQUEST_TABLE)


def queue_extraction(project: str, filename: str) -> bool:
    """Queue one raw file. Return False when it is already pending."""
    _ensure_table()
    return _db.execute_rowcount(_QUEUE_REQUEST, (project, filename)) == 1


def list_project_requests(project: str) -> list[dict[str, Any]]:
    _ensure_table()
    return _db.fetchall(_SELECT_BY_PROJECT, (project,))


def delete_extraction_request(project: str, filename: str) -> None:
    _ensure_table()
    _db.execute(_DELETE_REQUEST, (project, filename))
