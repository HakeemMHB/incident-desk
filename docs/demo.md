# Demo procedure

Every command runs from the repository root. Nothing to configure first.

## 1. Start

```bash
git clone https://github.com/HakeemMHB/incident-desk.git
cd incident-desk
docker compose up --build
```

Open http://localhost:8000. Wait for all three services to show as healthy:

```bash
docker compose ps
```

## 2. Submit reports

- A valid report: redirected to the dashboard with a confirmation.
- An empty title, then a 3,000-character description: refused, with a message
  per field (HTTP 422).
- A title containing `<script>alert(1)</script>`: displayed as text.
- More than 5 submissions in a minute: refused politely (HTTP 429).

Live feed: open the dashboard in a second window, submit a report in the
first one, and watch it appear without reloading.

## 3. Terminal 1: PostgreSQL

```bash
docker compose exec postgres psql -U incidents -d incidents
```

```sql
\d reports
SELECT id, title, category, severity, created_at FROM reports ORDER BY id DESC;
SELECT severity, count(*) FROM reports GROUP BY severity;
```

The CHECK constraints refuse invalid data even outside the application:

```sql
INSERT INTO reports (title, category, severity, description)
VALUES ('test', 'phishing', 'apocalypse', 'x');
-- ERROR: new row violates check constraint
```

## 4. Terminal 2: Redis

```bash
docker compose exec redis redis-cli
```

```text
KEYS incidents:*
LRANGE incidents:recent 0 -1
HGETALL incidents:severity_counts
KEYS incidents:ratelimit:*
TTL <one of the ratelimit keys>
SUBSCRIBE incidents:live          # then submit a report in the browser
```

## 5. Persistence

```bash
docker compose down        # never "down -v": -v deletes the volumes
docker compose up
```

The reports are still in both databases.

## 6. Resilience

```bash
# Redis down: reports are still accepted, the dashboard reads PostgreSQL.
docker compose stop redis
curl -s localhost:8000/readyz      # {"postgres":"up","redis":"down"}
docker compose start redis

# Redis loses its data: it is rebuilt from PostgreSQL.
docker compose exec redis redis-cli FLUSHALL
# reload the dashboard: everything is back
```

## 7. Security checks

```bash
docker compose exec app whoami                    # app, not root
curl -sI localhost:8000/ | grep -i -E "content-security|x-frame"
curl -s -o /dev/null -w "%{http_code}\n" localhost:8000/docs   # 404
```

## 8. Tests

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```
