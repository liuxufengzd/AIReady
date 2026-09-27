"""Shared document identity for extraction and publish."""

import dagster as dg

_PARTITION_SEPARATOR = "/"

document_partitions = dg.DynamicPartitionsDefinition(name="raw_documents")


def document_key(project: str, filename: str) -> str:
    """Partition key for one raw file, ``{project}/{filename}``."""
    if _PARTITION_SEPARATOR in project or _PARTITION_SEPARATOR in filename:
        raise ValueError(f"Invalid document identity: {project!r} / {filename!r}")
    return f"{project}{_PARTITION_SEPARATOR}{filename}"


def parse_document_key(key: str) -> tuple[str, str]:
    """Split a document partition key into ``(project, filename)``."""
    project, separator, filename = key.partition(_PARTITION_SEPARATOR)
    if not separator or not project or not filename or _PARTITION_SEPARATOR in filename:
        raise ValueError(f"Invalid document partition key: {key!r}")
    return project, filename
