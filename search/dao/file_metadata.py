"""File metadata this service reads when it indexes a published file."""

from typing import Any

from common.db import DBClient

_db = DBClient()

_SELECT_FILE_METADATA = """
SELECT project, filename, mime_type, size, extension
FROM file_metadata
WHERE project = %s AND filename = %s
"""


def get_file_metadata(project: str, filename: str) -> dict[str, Any] | None:
    """Return one file's metadata, or None when that file has not been stored."""
    return _db.fetchone(_SELECT_FILE_METADATA, (project, filename))
