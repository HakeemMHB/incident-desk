"""Redis: the fast layer.

Redis plays three roles, each with a simple data structure:

  incidents:recent              LIST    the latest reports (JSON), capped
  incidents:severity_counts     HASH    one counter per severity
  incidents:ratelimit:<ip>:<n>  STRING  submissions of one IP in minute <n>
  incidents:live                PUB/SUB new reports, pushed to open pages

Everything in Redis can be rebuilt from PostgreSQL (see `rebuild`).
"""

import json
import time
from dataclasses import dataclass

import redis

from app.config import Settings
from app.models import Severity, StoredReport

RECENT_KEY = "incidents:recent"
COUNTS_KEY = "incidents:severity_counts"
RATE_LIMIT_PREFIX = "incidents:ratelimit"
LIVE_CHANNEL = "incidents:live"
RATE_LIMIT_WINDOW = 60  # seconds


def make_client(settings: Settings) -> redis.Redis:
    return redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password.get_secret_value() if settings.redis_password else None,
        decode_responses=True,
        socket_timeout=2,
        socket_connect_timeout=2,
    )


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after: int  # seconds before the window resets


class ReportCache:
    def __init__(self, client: redis.Redis, recent_limit: int, rate_limit: int) -> None:
        self.client = client
        self.recent_limit = recent_limit
        self.rate_limit = rate_limit

    def push(self, report: StoredReport) -> None:
        """Record a new report. MULTI/EXEC: all four commands or none."""
        payload = json.dumps(report.to_public())
        pipe = self.client.pipeline(transaction=True)
        pipe.lpush(RECENT_KEY, payload)
        pipe.ltrim(RECENT_KEY, 0, self.recent_limit - 1)  # the list never grows forever
        pipe.hincrby(COUNTS_KEY, report.severity.value, 1)
        pipe.publish(LIVE_CHANNEL, payload)
        pipe.execute()

    def recent(self) -> list[StoredReport]:
        items = self.client.lrange(RECENT_KEY, 0, self.recent_limit - 1)
        return [StoredReport(**json.loads(item)) for item in items]

    def severity_counts(self) -> dict[Severity, int]:
        raw = self.client.hgetall(COUNTS_KEY)
        return {Severity(key): int(value) for key, value in raw.items()}

    def hit_rate_limit(self, client_ip: str) -> RateLimitResult:
        """Fixed window: one counter per IP and per minute, which expires on its own.

        INCR is atomic, so two simultaneous requests can never both read the
        same old value. EXPIRE makes Redis delete the counter itself: no
        cleanup job, unlike a table of attempts in PostgreSQL.
        """
        now = int(time.time())
        window = now // RATE_LIMIT_WINDOW
        key = f"{RATE_LIMIT_PREFIX}:{client_ip}:{window}"
        pipe = self.client.pipeline(transaction=True)
        pipe.incr(key)
        pipe.expire(key, RATE_LIMIT_WINDOW, nx=True)
        count, _ = pipe.execute()
        retry_after = RATE_LIMIT_WINDOW - (now % RATE_LIMIT_WINDOW)
        return RateLimitResult(allowed=count <= self.rate_limit, retry_after=retry_after)

    def is_empty(self) -> bool:
        return self.client.exists(RECENT_KEY, COUNTS_KEY) == 0

    def rebuild(self, recent: list[StoredReport], counts: dict[Severity, int]) -> None:
        """Replace the cached data with what PostgreSQL holds."""
        pipe = self.client.pipeline(transaction=True)
        pipe.delete(RECENT_KEY, COUNTS_KEY)
        if recent:
            # `recent` is newest first; RPUSH keeps that order in the list.
            pipe.rpush(RECENT_KEY, *(json.dumps(r.to_public()) for r in recent))
        if counts:
            pipe.hset(COUNTS_KEY, mapping={s.value: n for s, n in counts.items()})
        pipe.execute()

    def ping(self) -> bool:
        return bool(self.client.ping())
