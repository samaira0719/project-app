"""FastAPI application factory - API plus static frontend."""

from pathlib import Path
import asyncio
import json
from contextlib import asynccontextmanager
from time import perf_counter

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .database import init_db
from .routers import auth, chat, feedback, insights, privacy, survey, tasks
from .tracking import cleanup_worker, logged_ip, router as tracking_router

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


def create_app() -> FastAPI:
    settings = get_settings()
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = asyncio.Event()
        worker = asyncio.create_task(cleanup_worker(stop)) if settings.tracking_enabled else None
        app.state.tracking_stop = stop
        try:
            yield
        finally:
            stop.set()
            if worker:
                await worker

    app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.debug else [],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    init_db()

    app.include_router(auth.router)
    app.include_router(privacy.router)
    app.include_router(survey.router)
    app.include_router(tasks.router)
    app.include_router(insights.router)
    app.include_router(chat.router)
    app.include_router(feedback.router)
    app.include_router(tracking_router)

    @app.middleware("http")
    async def structured_request_log(request, call_next):
        started = perf_counter()
        response = await call_next(request)
        if request.url.path != "/health" and not request.url.path.startswith(("/static/", "/api/usage/")):
            print(json.dumps({
                "event": "request", "ip": logged_ip(request), "method": request.method,
                "path": request.url.path, "status": response.status_code,
                "latency_ms": round((perf_counter() - started) * 1000, 2),
            }, separators=(",", ":")), flush=True)
        return response

    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        # The shell page carries the versioned script tag, so a cached copy
        # of it would keep loading an old app.js. Never let it be cached.
        return FileResponse(
            FRONTEND_DIR / "index.html",
            headers={"Cache-Control": "no-store, must-revalidate"},
        )

    @app.get("/health", include_in_schema=False)
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
