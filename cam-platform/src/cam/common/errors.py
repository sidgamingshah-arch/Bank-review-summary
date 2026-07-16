"""Uniform error envelope: {"error": {"code", "message", "details"}} (contracts.md)."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details

    @staticmethod
    def not_found(what: str = "resource") -> "ApiError":
        return ApiError(404, "not_found", f"{what} not found")

    @staticmethod
    def forbidden(message: str = "insufficient permissions") -> "ApiError":
        return ApiError(403, "forbidden", message)

    @staticmethod
    def unauthorized(message: str = "authentication required") -> "ApiError":
        return ApiError(401, "unauthorized", message)

    @staticmethod
    def validation(message: str, details: Any = None) -> "ApiError":
        return ApiError(422, "validation_error", message, details)

    @staticmethod
    def conflict(message: str, code: str = "conflict") -> "ApiError":
        return ApiError(409, code, message)


def _envelope(code: str, message: str, details: Any = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status, content=_envelope(exc.code, exc.message, exc.details))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content=_envelope("validation_error", "invalid request", exc.errors()))
