"""Web layer and failure policy, with in-memory fakes (no database needed)."""

import pytest

from app.models import DESCRIPTION_MAX_LENGTH


def test_form_page_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert 'method="post"' in response.text


def test_valid_report_is_stored_in_both_databases(client, repo, cache, valid_form):
    response = client.post("/", data=valid_form)

    assert response.status_code == 303
    assert response.headers["location"] == "/reports?submitted=1"
    assert repo.rows[0].title == valid_form["title"]
    assert cache.recent_items[0].title == valid_form["title"]
    assert cache.counts["high"] == 1


def test_report_appears_on_dashboard(client, valid_form):
    client.post("/", data=valid_form)

    response = client.get("/reports")

    assert response.status_code == 200
    assert valid_form["title"] in response.text


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("title", ""),
        ("title", "     "),
        ("severity", "apocalypse"),
        ("category", "not-a-category"),
        ("description", "x" * (DESCRIPTION_MAX_LENGTH + 1)),
    ],
)
def test_invalid_report_is_refused_with_a_message(client, repo, valid_form, field, bad_value):
    response = client.post("/", data={**valid_form, field: bad_value})

    assert response.status_code == 422
    assert "Please correct the highlighted fields." in response.text
    assert repo.rows == []


def test_missing_field_is_refused(client, repo, valid_form):
    del valid_form["severity"]

    response = client.post("/", data=valid_form)

    assert response.status_code == 422
    assert repo.rows == []


def test_html_in_a_report_is_shown_as_text(client, valid_form):
    payload = "<script>alert('xss')</script>"
    client.post("/", data={**valid_form, "title": payload})

    response = client.get("/reports")

    assert payload not in response.text
    assert "&lt;script&gt;" in response.text


def test_rate_limit_refuses_politely(client, cache, repo, valid_form):
    cache.rate_limit = 2

    statuses = [client.post("/", data=valid_form).status_code for _ in range(3)]
    blocked = client.post("/", data=valid_form)

    assert statuses == [303, 303, 429]
    assert blocked.headers["retry-after"] == "42"
    assert "Too many reports" in blocked.text
    assert len(repo.rows) == 2


def test_invalid_attempts_count_towards_the_limit(client, cache, repo, valid_form):
    cache.rate_limit = 1
    client.post("/", data={**valid_form, "title": ""})

    response = client.post("/", data=valid_form)

    assert response.status_code == 429
    assert repo.rows == []


def test_security_headers_are_set(client):
    response = client.get("/")
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_api_docs_are_not_exposed(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


# --- Failure policy (ADR 003) ---------------------------------------------


def test_redis_down_report_is_still_saved(client, repo, cache, valid_form):
    cache.down = True

    response = client.post("/", data=valid_form)

    assert response.status_code == 303
    assert len(repo.rows) == 1


def test_redis_down_dashboard_reads_postgres(client, cache, valid_form):
    client.post("/", data=valid_form)
    cache.down = True

    response = client.get("/reports")

    assert response.status_code == 200
    assert valid_form["title"] in response.text
    assert "Fast cache unavailable" in response.text


def test_postgres_down_returns_503_without_details(client, repo, cache, valid_form):
    repo.down = True

    response = client.post("/", data=valid_form)

    assert response.status_code == 503
    assert "postgres down" not in response.text
    assert cache.recent_items == []


def test_cache_is_rebuilt_from_postgres(service, repo, cache, valid_form, client):
    for _ in range(3):
        client.post("/", data=valid_form)
    cache.recent_items, cache.counts = [], cache.counts.__class__()

    assert service.sync_cache() is True
    assert len(cache.recent_items) == 3
    assert cache.counts["high"] == 3


# --- Technical endpoints ----------------------------------------------------


def test_liveness_never_touches_databases(client, repo, cache):
    repo.down = cache.down = True
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_readiness_when_both_databases_answer(client):
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"postgres": "up", "redis": "up"}


def test_readiness_names_the_failing_database(client, cache):
    cache.down = True
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"postgres": "up", "redis": "down"}
