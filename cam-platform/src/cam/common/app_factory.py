"""Standard FastAPI app shell used by every service: correlation middleware,
error envelope, /healthz. Keeps the services uniform and boring.
"""
from __future__ import annotations

from fastapi import FastAPI

from .config import Settings
from .correlation import CorrelationMiddleware
from .errors import install_error_handlers


def create_app(settings: Settings, title: str) -> FastAPI:
    app = FastAPI(title=title, version="0.1.0", docs_url="/docs")
    app.add_middleware(CorrelationMiddleware)
    install_error_handlers(app)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "service": settings.service_name}

    return app
