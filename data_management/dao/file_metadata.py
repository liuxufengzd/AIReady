"""File metadata dataprep stores when it publishes a document."""

from typing import Any

from data_management.dao import db as _db

_CREATE_FILE_METADATA = """
CREATE TABLE IF NOT EXISTS file_metadata (
    project TEXT NOT NULL,
    filename TEXT NOT NULL,
    mime_type TEXT,
    size BIGINT,
    extension JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (project, filename)
)
"""

_SELECT_BY_PROJECT = """
SELECT project, filename, mime_type, size, extension
FROM file_metadata
WHERE project = %s
"""

_DELETE_FILE_METADATA = """
DELETE FROM file_metadata
WHERE project = %s AND filename = %s
"""


def _ensure_table() -> None:
    _db.execute(_CREATE_FILE_METADATA)


def list_project_file_metadata(project: str) -> list[dict[str, Any]]:
    _ensure_table()
    return _db.fetchall(_SELECT_BY_PROJECT, (project,))


def delete_file_metadata(project: str, filename: str) -> None:
    _ensure_table()
    _db.execute(_DELETE_FILE_METADATA, (project, filename))
