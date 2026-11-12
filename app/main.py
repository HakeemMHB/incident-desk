"""Web layer: pages, form handling, live feed and technical endpoints.

Path of a submission (POST /):
1. Rate limiting, counted in Redis. Every attempt counts, valid or not.
2. Validation against `ReportIn`. If it fails, the form comes back with a
   message per field (HTTP 422).
3. Storage: PostgreSQL first, then Redis (see app/service.py).
4. Redirect to /reports (HTTP 303): refreshing the page cannot submit the
   form twice (Post/Redirect/Get).
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import redis
import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.db.cache import LIVE_CHANNEL, ReportCache, make_client
from app.db.postgres import ReportRepository, make_pool
from app.models import DESCRIPTION_MAX_LENGTH, TITLE_MAX_LENGTH, Category, ReportIn, Severity
from app.service import IncidentService, StorageUnavailable

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("incident_desk")

BASE_DIR = Path(__file__).resolve().parent

# Jinja2 escapes every {{ value }} in .html templates: user input is shown
# as text and never runs as HTML or JavaScript.
templates = Jinja2Templates(directory=BASE_DIR / "templates")

FIELD_ERRORS = {
    "title": f"A title is required (at most {TITLE_MAX_LENGTH} characters).",
    "category": "Choose a category from the list.",
    "severity": "Choose a severity from the list.",
    "description": f"A description is required (at most {DESCRIPTION_MAX_LENGTH} characters).",
}

SECURITY_HEADERS = {
    # Only our own scripts, styles and connections: injected scripts cannot run.
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def build_service(settings: Settings) -> IncidentService:
    pool = make_pool(settings)
    pool.open(wait=True, timeout=30)
    repo = ReportRepository(pool)
    repo.create_schema()
    cache = ReportCache(make_client(settings), settings.recent_limit, settings.rate_limit_per_minute)
    return IncidentService(repo, cache, settings.recent_limit)


def make_live_client(settings: Settings) -> aioredis.Redis:
    # No read timeout: a live-feed connection waits for messages on purpose.
    return aioredis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password.get_secret_value() if settings.redis_password else None,
        decode_responses=True,
        socket_connect_timeout=2,
    )


def create_app(service: IncidentService | None = None) -> FastAPI:
    """Build the application. Tests pass their own service; production builds the real one."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if service is not None:
            app.state.service, app.state.live = service, None
            yield
            return

        settings = get_settings()
        real = build_service(settings)
        try:
            real.sync_cache()
        except redis.RedisError:
            log.warning("Redis unavailable at startup: running in degraded mode")
        app.state.service, app.state.live = real, make_live_client(settings)
        yield
        await app.state.live.aclose()
        real.cache.client.close()
        real.repo.pool.close()

    # No /docs page: there is no public API, and it would list every route.
    app = FastAPI(title="Incident Desk", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    app.add_exception_handler(StorageUnavailable, storage_unavailable)
    register_routes(app)
    return app


def get_service(request: Request) -> IncidentService:
    return request.app.state.service


ServiceDep = Annotated[IncidentService, Depends(get_service)]


def client_ip(request: Request) -> str:
    # Direct connection: this is the real client. Behind a reverse proxy, it
    # would be the proxy's address (uvicorn --proxy-headers would be needed).
    return request.client.host if request.client else "unknown"


def render(request: Request, name: str, status_code: int = 200, headers=None, **context) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request, name=name, context=context, status_code=status_code, headers=headers
    )


def render_form(request: Request, values=None, errors=None, status_code: int = 200) -> HTMLResponse:
    return render(
        request,
        "form.html",
        status_code=status_code,
        categories=list(Category),
        severities=list(Severity),
        values=values or {},
        errors=errors or {},
        title_max=TITLE_MAX_LENGTH,
        description_max=DESCRIPTION_MAX_LENGTH,
    )


def storage_unavailable(request: Request, exc: StorageUnavailable) -> HTMLResponse:
    return render(
        request,
        "error.html",
        status_code=503,
        title="Service unavailable",
        message="The report could not be processed right now. Please try again in a moment.",
    )


def register_routes(app: FastAPI) -> None:
    @app.get("/", response_class=HTMLResponse)
    def show_form(request: Request):
        return render_form(request)

    @app.post("/", response_class=HTMLResponse)
    def submit_report(
        request: Request,
        service: ServiceDep,
        # Default "": a missing field becomes a clear message, not a raw JSON error.
        title: Annotated[str, Form()] = "",
        category: Annotated[str, Form()] = "",
        severity: Annotated[str, Form()] = "",
        description: Annotated[str, Form()] = "",
    ):
        limit = service.check_rate_limit(client_ip(request))
        if not limit.allowed:
            return render(
                request,
                "error.html",
                status_code=429,
                headers={"Retry-After": str(limit.retry_after)},
                title="Too many reports",
                message=f"You sent too many reports in a short time. "
                f"Please try again in {limit.retry_after} seconds.",
            )

        values = {"title": title, "category": category, "severity": severity, "description": description}
        try:
            report = ReportIn(**values)
        except ValidationError as exc:
            errors = {
                str(err["loc"][0]): FIELD_ERRORS[str(err["loc"][0])]
                for err in exc.errors()
                if err["loc"] and str(err["loc"][0]) in FIELD_ERRORS
            }
            return render_form(request, values=values, errors=errors, status_code=422)

        service.submit(report)
        return RedirectResponse(url="/reports?submitted=1", status_code=303)

    @app.get("/reports", response_class=HTMLResponse)
    def show_reports(request: Request, service: ServiceDep, submitted: bool = False):
        board = service.dashboard()
        max_category = max(board.category_counts.values(), default=0)
        return render(
            request,
            "reports.html",
            board=board,
            severities=list(Severity),
            categories=sorted(Category, key=lambda c: -board.category_counts.get(c, 0)),
            max_category=max_category,
            submitted=submitted,
        )

    @app.get("/events")
    async def live_events(request: Request):
        """Server-Sent Events: each new report is pushed to open pages via Redis Pub/Sub."""
        live: aioredis.Redis | None = request.app.state.live
        if live is None:
            return JSONResponse({"detail": "live feed unavailable"}, status_code=503)
        try:
            await live.ping()
        except redis.RedisError:
            return JSONResponse({"detail": "live feed unavailable"}, status_code=503)

        async def stream():
            pubsub = live.pubsub()
            await pubsub.subscribe(LIVE_CHANNEL)
            try:
                yield "retry: 5000\n\n"
                while not await request.is_disconnected():
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15.0)
                    if message is None:
                        yield ": keep-alive\n\n"
                    else:
                        yield f"event: report\ndata: {message['data']}\n\n"
            except redis.RedisError:
                log.warning("Live feed: Redis connection lost")
            finally:
                await pubsub.aclose()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/healthz")
    def liveness():
        """The process is alive. Never touches the databases."""
        return {"status": "alive"}

    @app.get("/readyz")
    def readiness(service: ServiceDep):
        """Both databases answer. 503 otherwise, with the failing one named."""
        checks = service.readiness()
        return JSONResponse(
            status_code=200 if all(checks.values()) else 503,
            content={name: "up" if ok else "down" for name, ok in checks.items()},
        )


app = create_app()
