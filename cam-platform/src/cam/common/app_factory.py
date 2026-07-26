"""Standard FastAPI app shell used by every service: correlation middleware,
error envelope, /healthz. Keeps the services uniform and boring.
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI

from .config import Settings
from .correlation import CorrelationMiddleware
from .errors import install_error_handlers


def _configure_logging() -> None:
    """Attach a stdout handler to the ``cam`` logger at CAM_LOG_LEVEL so app
    logs (e.g. per-agent token usage) actually surface — uvicorn does not
    configure the root logger, so INFO records would otherwise be dropped by
    the WARNING-level last-resort handler. Unset (as in unit tests) -> no
    handler, unchanged behaviour."""
    level = os.environ.get("CAM_LOG_LEVEL")
    if not level:
        return
    cam_log = logging.getLogger("cam")
    if not any(getattr(h, "_cam_handler", False) for h in cam_log.handlers):
        handler = logging.StreamHandler()
        handler._cam_handler = True  # type: ignore[attr-defined]
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"))
        cam_log.addHandler(handler)
        cam_log.propagate = False
    cam_log.setLevel(level.upper())


def create_app(settings: Settings, title: str) -> FastAPI:
    _configure_logging()
    app = FastAPI(title=title, version="0.1.0", docs_url="/docs")
    app.add_middleware(CorrelationMiddleware)
    install_error_handlers(app)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "service": settings.service_name}

    return app
