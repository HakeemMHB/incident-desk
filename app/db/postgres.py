"""PostgreSQL: the source of truth.

Every report is stored here, in full. All queries are parameterized: user
input is sent separately from the SQL text, so it can never be read as SQL.
"""

from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import Settings
from app.models import (
    DESCRIPTION_MAX_LENGTH,
    TITLE_MAX_LENGTH,
    Category,
    ReportIn,
    Severity,
    StoredReport,
)


def _sql_list(values) -> str:
    # Only used with our own enum values, never with user input.
    return ", ".join(f"'{value.value}'" for value in values)


SCHEMA = f"""
CREATE TABLE IF NOT EXISTS reports (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title       TEXT NOT NULL
                CHECK (char_length(btrim(title)) BETWEEN 1 AND {TITLE_MAX_LENGTH}),
    category    TEXT NOT NULL CHECK (category IN ({_sql_list(Category)})),
    severity    TEXT NOT NULL CHECK (severity IN ({_sql_list(Severity)})),
    description TEXT NOT NULL
                CHECK (char_length(btrim(description)) BETWEEN 1 AND {DESCRIPTION_MAX_LENGTH}),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS reports_created_at_idx ON reports (created_at DESC);
"""


def make_pool(settings: Settings) -> ConnectionPool:
    conninfo = make_conninfo(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        connect_timeout=5,
        client_encoding="utf8",
    )
    return ConnectionPool(
        conninfo,
        min_size=1,
        max_size=5,
        open=False,
        kwargs={"autocommit": True, "row_factory": dict_row},
    )


class ReportRepository:
    def __init__(self, pool: ConnectionPool) -> None:
        self.pool = pool

    def create_schema(self) -> None:
        with self.pool.connection() as conn:
            conn.execute(SCHEMA)

    def insert(self, report: ReportIn) -> StoredReport:
        with self.pool.connection() as conn:
            row = conn.execute(
                # %(name)s are placeholders: the values travel separately.
                """
                INSERT INTO reports (title, category, severity, description)
                VALUES (%(title)s, %(category)s, %(severity)s, %(description)s)
                RETURNING id, title, category, severity, description, created_at
                """,
                report.model_dump(mode="json"),
            ).fetchone()
        return StoredReport(**row)

    def recent(self, limit: int) -> list[StoredReport]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT id, title, category, severity, description, created_at
                FROM reports
                ORDER BY created_at DESC, id DESC
                LIMIT %(limit)s
                """,
                {"limit": limit},
            ).fetchall()
        return [StoredReport(**row) for row in rows]

    def count_by_severity(self) -> dict[Severity, int]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT severity, count(*) AS n FROM reports GROUP BY severity"
            ).fetchall()
        return {Severity(row["severity"]): row["n"] for row in rows}

    def count_by_category(self) -> dict[Category, int]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT category, count(*) AS n FROM reports GROUP BY category"
            ).fetchall()
        return {Category(row["category"]): row["n"] for row in rows}

    def ping(self) -> bool:
        with self.pool.connection(timeout=2) as conn:
            return conn.execute("SELECT 1 AS ok").fetchone()["ok"] == 1
