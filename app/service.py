"""Business logic: how the two databases work together.

Failure policy (see docs/adr/003-keep-working-when-redis-is-down.md):
- PostgreSQL down: nothing can be saved, the request fails with 503.
- Redis down: the application keeps working. Reports are still saved in
  PostgreSQL, pages read from PostgreSQL, and rate limiting is skipped.
  A lost security report costs more than a few unthrottled requests.
"""

import logging
from dataclasses import dataclass, field

import psycopg
import redis

from app.db.cache import RateLimitResult, ReportCache
from app.db.postgres import ReportRepository
from app.models import Category, ReportIn, Severity, StoredReport

log = logging.getLogger("incident_desk")


class StorageUnavailable(Exception):
    """The source of truth (PostgreSQL) cannot be reached."""


@dataclass
class Dashboard:
    recent: list[StoredReport]
    severity_counts: dict[Severity, int]
    category_counts: dict[Category, int]
    cache_ok: bool
    total: int = field(init=False)

    def __post_init__(self) -> None:
        self.total = sum(self.severity_counts.values())


class IncidentService:
    def __init__(self, repo: ReportRepository, cache: ReportCache, recent_limit: int) -> None:
        self.repo = repo
        self.cache = cache
        self.recent_limit = recent_limit

    def check_rate_limit(self, client_ip: str) -> RateLimitResult:
        try:
            return self.cache.hit_rate_limit(client_ip)
        except redis.RedisError:
            log.warning("Redis unavailable: rate limiting skipped for this request")
            return RateLimitResult(allowed=True, retry_after=0)

    def submit(self, report: ReportIn) -> StoredReport:
        # 1. Source of truth first. If this fails, nothing was saved anywhere.
        try:
            stored = self.repo.insert(report)
        except psycopg.Error as exc:
            log.error("PostgreSQL insert failed: %s", type(exc).__name__)
            raise StorageUnavailable from exc
        # 2. Then the cache. If this fails, the report is still safe.
        try:
            self.cache.push(stored)
        except redis.RedisError:
            log.warning("Redis unavailable: report %s saved in PostgreSQL only", stored.id)
        return stored

    def dashboard(self) -> Dashboard:
        try:
            category_counts = self.repo.count_by_category()
        except psycopg.Error as exc:
            raise StorageUnavailable from exc

        try:
            # Self-healing: if Redis came back empty (restart, lost volume),
            # refill it from PostgreSQL before reading.
            self.sync_cache()
            recent = self.cache.recent()
            severity_counts = self.cache.severity_counts()
            cache_ok = True
        except redis.RedisError:
            log.warning("Redis unavailable: reading the dashboard from PostgreSQL")
            try:
                recent = self.repo.recent(self.recent_limit)
                severity_counts = self.repo.count_by_severity()
            except psycopg.Error as exc:
                raise StorageUnavailable from exc
            cache_ok = False

        return Dashboard(recent, severity_counts, category_counts, cache_ok)

    def sync_cache(self, force: bool = False) -> bool:
        """Rebuild Redis from PostgreSQL. Returns True if a rebuild happened."""
        if not force and not self.cache.is_empty():
            return False
        recent = self.repo.recent(self.recent_limit)
        counts = self.repo.count_by_severity()
        self.cache.rebuild(recent, counts)
        log.info("Redis rebuilt from PostgreSQL: %d recent, %d total", len(recent), sum(counts.values()))
        return True

    def readiness(self) -> dict[str, bool]:
        return {"postgres": _safe(self.repo.ping), "redis": _safe(self.cache.ping)}


def _safe(ping) -> bool:
    try:
        return bool(ping())
    except Exception:
        return False
