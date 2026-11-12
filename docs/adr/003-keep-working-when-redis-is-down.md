# 3. Keep working when Redis is down

Date: 2026-11-06

## Status

Accepted. Answers the open question of ADR 001.

## Context

The application writes to two systems. PostgreSQL is the source of truth;
Redis holds derived data (recent reports, counters) and the rate-limiting
counters. Either can become unavailable. We must decide what the application
does in each case.

The portal collects security incidents. A report that is refused can mean a
phishing campaign or a stolen laptop nobody hears about.

## Considered Options

- **Fail closed**: refuse every submission while Redis is down. Rate limiting
  is never bypassed, but legitimate reports are lost during the outage.
- **Fail open**: accept submissions without rate limiting while Redis is
  down, and read pages from PostgreSQL.

## Decision Outcome

Chosen option: **fail open for Redis, fail closed for PostgreSQL.**

| Situation | Submissions | Dashboard | `/readyz` |
|---|---|---|---|
| Both up | Saved in PostgreSQL, then Redis | Read from Redis | 200 |
| Redis down | Saved in PostgreSQL only, no rate limit | Read from PostgreSQL, with a warning banner | 503 (`redis: down`) |
| PostgreSQL down | Refused (503): nothing can be saved | Unavailable (503) | 503 (`postgres: down`) |

When Redis comes back empty (restart without its volume, data lost), the
application rebuilds it from PostgreSQL automatically on the next dashboard
view, or on demand with `python -m app.sync_cache`.

### Consequences

- **Good:** no security report is lost because of a cache outage.
- **Good:** Redis is fully disposable: losing it never loses data.
- **Bad:** during a Redis outage, an attacker can submit without limit.
  Mitigations: the outage is logged and visible in `/readyz`, and every
  submission is still validated, so the worst case is spam in PostgreSQL.
- **Bad:** counters written during an outage are missing from Redis until the
  next full rebuild (`python -m app.sync_cache`).
