import json

import psycopg_pool
from psycopg.rows import dict_row

from common.logger import get_logger
from supervisor.model.thread import QAPair, Thread, ThreadSummary

logger = get_logger(__name__)


class ThreadStore:
    """Manages persistent conversation threads in PostgreSQL.

    The full Q&A history is kept in the ``body`` column forever.
    ``extracted_count`` tracks how many leading pairs have been compacted
    into the vector DB so that topic extraction is not repeated.
    The prompt window is limited separately in the service layer.
    """

    def __init__(self, pool: psycopg_pool.AsyncConnectionPool) -> None:
        self.pool = pool

    async def setup(self) -> None:
        """Create / migrate the threads table."""
        async with self.pool.connection() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS t_threads (
                    thread_id        TEXT        PRIMARY KEY,
                    project          TEXT        NOT NULL,
                    title            TEXT,
                    body             JSONB       NOT NULL DEFAULT '[]',
                    extracted_count  INTEGER     NOT NULL DEFAULT 0,
                    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS t_threads_project_updated
                ON t_threads (project, updated_at DESC)
            """)
        logger.info("ThreadStore setup complete")

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get(self, thread_id: str) -> Thread | None:
        async with self.pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute(
                "SELECT * FROM t_threads WHERE thread_id = %s",
                (thread_id,),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            return self._row_to_thread(row)

    async def list_threads(self, project: str, limit: int = 100) -> list[ThreadSummary]:
        async with self.pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute(
                """
                SELECT thread_id, project, title, created_at, updated_at
                FROM t_threads
                WHERE project = %s
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (project, limit),
            )
            rows = await cur.fetchall()
        return [
            ThreadSummary(
                thread_id=r["thread_id"],
                project=r["project"],
                title=r["title"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def get_or_create(self, thread_id: str, project: str) -> Thread:
        """Return the existing thread or insert a new empty one."""
        async with self.pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute(
                """
                INSERT INTO t_threads (thread_id, project, body)
                VALUES (%s, %s, '[]'::jsonb)
                ON CONFLICT (thread_id) DO UPDATE
                    SET updated_at = t_threads.updated_at
                RETURNING *
                """,
                (thread_id, project),
            )
            row = await cur.fetchone()
        return self._row_to_thread(row)

    async def append_qa(self, thread_id: str, qa: QAPair) -> None:
        """Append a single Q&A pair to the thread body."""
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                UPDATE t_threads
                SET body       = body || %s::jsonb,
                    updated_at = NOW()
                WHERE thread_id = %s
                """,
                (json.dumps([qa.model_dump()]), thread_id),
            )

    async def update_title(self, thread_id: str, title: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                UPDATE t_threads
                SET title = %s, updated_at = NOW()
                WHERE thread_id = %s
                """,
                (title, thread_id),
            )

    async def update_extracted_count(
        self, thread_id: str, extracted_count: int
    ) -> None:
        """Advance the extraction cursor after topics have been stored in vector DB."""
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                UPDATE t_threads
                SET extracted_count = GREATEST(extracted_count, %s),
                    updated_at      = NOW()
                WHERE thread_id = %s
                """,
                (extracted_count, thread_id),
            )

    async def delete_thread(self, thread_id: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "DELETE FROM t_threads WHERE thread_id = %s",
                (thread_id,),
            )

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_thread(row: dict) -> Thread:
        body = [QAPair(**qa) for qa in (row["body"] or [])]
        return Thread(
            thread_id=row["thread_id"],
            project=row["project"],
            title=row["title"],
            body=body,
            extracted_count=row.get("extracted_count", 0),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
