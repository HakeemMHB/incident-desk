"""Integration tests against real PostgreSQL and Redis.

Skipped unless INTEGRATION_TESTS=1. Run them against the Compose databases:
    INTEGRATION_TESTS=1 POSTGRES_PASSWORD=... REDIS_PASSWORD=... pytest
(the CI runs them with service containers).
"""

import os
import time

import psycopg
import pytest

from app.config import Settings
from app.db.cache import RECENT_KEY, ReportCache, make_client
from app.db.postgres import ReportRepository, make_pool
from app.models import ReportIn, Severity
from app.service import IncidentService

pytestmark = pytest.mark.skipif(
    os.environ.get("INTEGRATION_TESTS") != "1", reason="set INTEGRATION_TESTS=1 to run"
)


@pytest.fixture
def real():
    settings = Settings()
    pool = make_pool(settings)
    pool.open(wait=True, timeout=10)
    repo = ReportRepository(pool)
    repo.create_schema()
    with pool.connection() as conn:
        conn.execute("TRUNCATE reports RESTART IDENTITY")
    client = make_client(settings)
    client.flushdb()
    cache = ReportCache(client, recent_limit=3, rate_limit=2)
    yield IncidentService(repo, cache, recent_limit=3)
    client.close()
    pool.close()


def report(title="Phishing attempt", severity="high") -> ReportIn:
    return ReportIn(title=title, category="phishing", severity=severity, description="Details")


def test_report_lands_in_postgres_and_redis(real):
    stored = real.submit(report())

    assert real.repo.recent(10)[0].id == stored.id
    assert real.cache.recent()[0].id == stored.id
    assert real.cache.severity_counts() == {Severity.HIGH: 1}


def test_recent_list_is_capped(real):
    for i in range(5):
        real.submit(report(title=f"Report {i}"))

    assert real.cache.client.llen(RECENT_KEY) == 3
    assert [r.title for r in real.cache.recent()] == ["Report 4", "Report 3", "Report 2"]


def test_sql_injection_is_stored_as_plain_text(real):
    payload = "x'); DROP TABLE reports; --"
    real.submit(report(title=payload))

    assert real.repo.recent(1)[0].title == payload
    assert real.repo.ping()  # the table is still there


def test_database_rejects_invalid_rows_on_its_own(real):
    with pytest.raises(psycopg.errors.CheckViolation):
        with real.repo.pool.connection() as conn:
            conn.execute(
                "INSERT INTO reports (title, category, severity, description) "
                "VALUES ('t', 'phishing', 'apocalypse', 'd')"
            )


def test_rate_limit_counter_expires_by_itself(real):
    results = [real.cache.hit_rate_limit("203.0.113.7") for _ in range(3)]

    assert [r.allowed for r in results] == [True, True, False]
    keys = real.cache.client.keys("incidents:ratelimit:203.0.113.7:*")
    assert len(keys) == 1
    assert 0 < real.cache.client.ttl(keys[0]) <= 60
    assert real.cache.hit_rate_limit("198.51.100.1").allowed  # other IPs are not affected


def test_cache_rebuilt_from_postgres_after_loss(real):
    for severity in ["low", "high", "high"]:
        real.submit(report(severity=severity))
    real.cache.client.flushdb()  # simulate a lost Redis volume

    assert real.sync_cache() is True
    assert real.cache.severity_counts() == {Severity.LOW: 1, Severity.HIGH: 2}
    assert len(real.cache.recent()) == 3
    assert real.sync_cache() is False  # nothing to do the second time


def test_readiness(real):
    assert real.readiness() == {"postgres": True, "redis": True}


def test_timestamps_are_utc(real):
    stored = real.submit(report())
    assert stored.created_at.utcoffset().total_seconds() == 0
    assert abs(stored.created_at.timestamp() - time.time()) < 60
