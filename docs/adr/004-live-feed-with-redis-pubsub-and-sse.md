# 4. Live dashboard with Redis Pub/Sub and Server-Sent Events

Date: 2026-11-06

## Status

Accepted

## Context

The dashboard should show new reports as they arrive, without reloading the
page: an IT team watching incidents needs to see a critical report at once.
Data only flows one way, from the server to the browser.

## Considered Options

- **Polling**: the page asks the server every few seconds. Simple, but most
  requests return nothing and each one hits the databases.
- **WebSocket**: two-way channel. More than we need, and needs a dedicated
  protocol and library.
- **Server-Sent Events (SSE) fed by Redis Pub/Sub**: one long HTTP response
  per open page; the server writes an event each time Redis publishes one.

## Decision Outcome

Chosen option: **SSE fed by Redis Pub/Sub.**

- When a report is saved, the same Redis transaction that updates the cache
  publishes it on the `incidents:live` channel.
- `GET /events` subscribes to that channel and forwards each message to the
  browser. Browsers reconnect on their own if the connection drops.
- The page inserts the report with `textContent`, never `innerHTML`, so the
  live feed cannot reintroduce XSS.

Redis now plays three roles: cache, rate limiter and message broker. Pub/Sub
also works when several application instances run: each one subscribes to the
same channel, so every page gets every report.

### Consequences

- **Good:** instant updates with plain HTTP and a few lines of JavaScript.
- **Good:** works across multiple app instances, unlike an in-memory queue.
- **Bad:** Pub/Sub is fire-and-forget: a page that is offline when a report
  is published misses it until the next reload. Acceptable here, since the
  page loads the current state from Redis or PostgreSQL first.
- **Bad:** each open page holds one connection to the app and one to Redis.
