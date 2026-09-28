"""File metadata written when a document is published."""

from typing import Any

from psycopg.types.json import Jsonb

from common.db import DBClient

_db = DBClient()

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

# A null extension inserts {} and, on conflict, keeps the extension already stored.
_UPSERT_FILE_METADATA = """
INSERT INTO file_metadata (project, filename, mime_type, size, extension)
VALUES (%s, %s, %s, %s, COALESCE(%s, '{}'::jsonb))
ON CONFLICT (project, filename) DO UPDATE
SET mime_type = EXCLUDED.mime_type,
    size = EXCLUDED.size,
    extension = COALESCE(%s, file_metadata.extension)
"""


def _ensure_file_metadata_table() -> None:
    _db.execute(_CREATE_FILE_METADATA)


def _jsonb(extension: dict[str, Any] | None) -> Jsonb | None:
    if extension is None:
        return None
    return Jsonb(extension)


def save_file_metadata(
    project: str,
    filename: str,
    mime_type: str | None,
    size: int | None,
    extension: dict[str, Any] | None = None,
) -> None:
    """Insert or refresh one file's metadata.

    ``extension`` is optional. Omit it to store ``{}`` on insert and to leave
    an existing extension unchanged when the same file is published again.
    """
    _ensure_file_metadata_table()
    _db.execute(
        _UPSERT_FILE_METADATA,
        (
            project,
            filename,
            mime_type,
            size,
            _jsonb(extension),
            _jsonb(extension),
        ),
    )
