"""Test fixtures.

Unit tests use the real `IncidentService` and web layer, with in-memory fakes
of PostgreSQL and Redis. They run with a single `pytest`, no database needed.
Each fake can be switched "down" to test the failure policy.
"""

from collections import Counter
from datetime import UTC, datetime

import psycopg
import pytest
import redis
from fastapi.testclient import TestClient

from app.db.cache import RateLimitResult
from app.main import create_app
from app.models import ReportIn, StoredReport
from app.service import IncidentService


class FakeRepo:
    def __init__(self) -> None:
        self.rows: list[StoredReport] = []
        self.down = False

    def _check(self) -> None:
        if self.down:
            raise psycopg.OperationalError("postgres down")

    def insert(self, report: ReportIn) -> StoredReport:
        self._check()
        stored = StoredReport(id=len(self.rows) + 1, created_at=datetime.now(UTC), **report.model_dump())
        self.rows.append(stored)
        return stored

    def recent(self, limit: int) -> list[StoredReport]:
        self._check()
        return list(reversed(self.rows))[:limit]

    def count_by_severity(self):
        self._check()
        return dict(Counter(r.severity for r in self.rows))

    def count_by_category(self):
        self._check()
        return dict(Counter(r.category for r in self.rows))

    def ping(self) -> bool:
        self._check()
        return True


class FakeCache:
    def __init__(self, rate_limit: int = 5) -> None:
        self.recent_items: list[StoredReport] = []
        self.counts: Counter = Counter()
        self.attempts: Counter = Counter()
        self.rate_limit = rate_limit
        self.down = False

    def _check(self) -> None:
        if self.down:
            raise redis.ConnectionError("redis down")

    def push(self, report: StoredReport) -> None:
        self._check()
        self.recent_items.insert(0, report)
        del self.recent_items[10:]
        self.counts[report.severity] += 1

    def recent(self):
        self._check()
        return list(self.recent_items)

    def severity_counts(self):
        self._check()
        return dict(self.counts)

    def hit_rate_limit(self, client_ip: str) -> RateLimitResult:
        self._check()
        self.attempts[client_ip] += 1
        return RateLimitResult(allowed=self.attempts[client_ip] <= self.rate_limit, retry_after=42)

    def is_empty(self) -> bool:
        self._check()
        return not self.recent_items and not self.counts

    def rebuild(self, recent, counts) -> None:
        self._check()
        self.recent_items = list(recent)
        self.counts = Counter(counts)

    def ping(self) -> bool:
        self._check()
        return True


@pytest.fixture
def repo() -> FakeRepo:
    return FakeRepo()


@pytest.fixture
def cache() -> FakeCache:
    return FakeCache()


@pytest.fixture
def service(repo, cache) -> IncidentService:
    return IncidentService(repo, cache, recent_limit=10)


@pytest.fixture
def client(service):
    with TestClient(create_app(service), follow_redirects=False) as test_client:
        yield test_client


@pytest.fixture
def valid_form() -> dict[str, str]:
    return {
        "title": "Suspicious email asking for my password",
        "category": "phishing",
        "severity": "high",
        "description": "An email pretending to be IT support asked me to log in.",
    }
