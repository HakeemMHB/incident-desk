# 1. Use PostgreSQL and Redis for data storage

Date: 2026-10-12

## Status

Accepted

## Context

The application is an incident reporting portal, similar to an IT helpdesk
ticket form. Users submit a report through a web form (title, category,
severity, description). A second page lists the most recent reports and
counters per severity.

The assignment requires submitted data to be persisted in at least two
different storage systems, orchestrated with Docker Compose.

The data has two distinct needs:

- Every report must be stored durably and completely. All reports share the
  same fixed, well-defined structure.
- Some data is read often and changes constantly: the latest reports, the
  counters per severity, and the number of submissions per IP address over a
  short time window, used to limit abuse.

## Considered Options

- **PostgreSQL + Redis**
- **MongoDB + Redis**: MongoDB stores flexible, heterogeneous documents.
  Reports all share the same fixed fields, so this flexibility brings nothing,
  whereas a relational schema enforces the expected structure (types, required
  fields, allowed values).
- **PostgreSQL + Neo4j**: Neo4j is designed for data whose relationships are
  at the heart of the queries. Reports are independent from one another, so
  there is no relationship to traverse. Neo4j also does not address
  short-lived counters or rate limiting.

## Decision Outcome

Chosen option: **PostgreSQL + Redis**.

- **PostgreSQL is the source of truth.** Every report is stored in full, in a
  table whose schema enforces the expected structure.
- **Redis is the fast layer.** It holds a capped list of the 10 most recent
  reports, one counter per severity, and per-IP rate limiting. Redis suits rate
  limiting because incrementing a counter is atomic (`INCR`) and a key can
  expire on its own (`EXPIRE`), so the counting window resets without any
  cleanup job.

### Consequences

- **Good:** reports are stored durably, with their structure enforced by the
  database.
- **Good:** frequently read data and rate limiting are served from memory,
  without a database write on every request.
- **Bad:** two systems to run, configure, secure and back up instead of one.
- **Bad:** Redis holds data derived from PostgreSQL and can drift from it, for
  example if a report is saved in PostgreSQL but the Redis update fails. Since
  PostgreSQL is the source of truth, Redis can be rebuilt from it.
- **Bad:** Redis must be configured to persist to disk (AOF), otherwise recent
  reports and counters are lost on restart.
- **Open question:** whether submissions are accepted or rejected when Redis
  is unavailable will be decided while building the application.
  Resolved by [ADR 003](003-keep-working-when-redis-is-down.md).
