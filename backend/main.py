"""FastAPI application factory - API plus static frontend."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .database import init_db
from .routers import auth, chat, feedback, insights, privacy, survey, tasks

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="1.0.0")

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

    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "index.html")

    @app.get("/health", include_in_schema=False)
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
