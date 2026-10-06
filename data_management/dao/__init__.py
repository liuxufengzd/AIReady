"""PostgreSQL access for this service. Every DAO module shares one pool."""

from common.db import DBClient

db = DBClient()


def close() -> None:
    """Close this service's connection pool."""
    db.close()
