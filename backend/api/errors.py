"""Stable errors for /api routes; preserve legacy frontend error payloads."""

import psycopg2
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.exception_handlers import (
    http_exception_handler, request_validation_exception_handler,
)
from starlette.exceptions import HTTPException

from backend.api.schemas import ErrorDetail, ErrorResponse

HTTP_CODES = {
    400: "INVALID_REQUEST", 401: "UNAUTHENTICATED", 403: "FORBIDDEN",
    404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 409: "CONFLICT",
    413: "FILE_TOO_LARGE", 415: "UNSUPPORTED_MEDIA_TYPE",
    422: "VALIDATION_ERROR", 429: "RATE_LIMITED", 503: "SERVICE_UNAVAILABLE",
}


def is_api(request: Request) -> bool:
    return request.url.path == "/api" or request.url.path.startswith("/api/")


def error_response(request, status, code, message, details=None, headers=None):
    request_id = getattr(request.state, "request_id", uuid4().hex)
    request.state.error_code = code
    body = ErrorResponse(
        error=ErrorDetail(code=code, message=message, details=details or []),
        request_id=request_id,
    )
    return JSONResponse(
        status_code=status, content=body.model_dump(mode="json"),
        headers={**(headers or {}), "X-Request-ID": request_id},
    )


def install_error_handlers(app: FastAPI):
    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if not is_api(request):
            return await http_exception_handler(request, exc)
        return error_response(
            request, exc.status_code,
            getattr(exc, "error_code", HTTP_CODES.get(exc.status_code, "HTTP_ERROR")),
            str(exc.detail) if exc.status_code < 500 else "Service temporarily unavailable.",
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        if not is_api(request):
            return await request_validation_exception_handler(request, exc)
        # Exclude submitted values and validation context (may contain PII/secrets).
        details = [{"location": list(e["loc"][:1] if e["type"]=="extra_forbidden" else e["loc"][:2]),
                    "type": e["type"]} for e in exc.errors()]
        return error_response(request, 422, "VALIDATION_ERROR", "Request validation failed.", details)

    @app.exception_handler(psycopg2.Error)
    async def database_error(request: Request, exc: psycopg2.Error):
        return failure_response(request, exc)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        return failure_response(request, exc)


def failure_response(request, exc):
    if not is_api(request):
        return JSONResponse(status_code=500,content={"detail":"Internal server error"})
    if isinstance(exc,psycopg2.errors.QueryCanceled):
        return error_response(request,504,"DATABASE_TIMEOUT","Database operation timed out.")
    if isinstance(exc,(psycopg2.OperationalError,psycopg2.InterfaceError)):
        return error_response(request,503,"DATABASE_UNAVAILABLE","Database temporarily unavailable.")
    if isinstance(exc,psycopg2.IntegrityError):
        return error_response(request,409,"DATABASE_CONFLICT","Database change conflicts with existing state.")
    return error_response(request,500,"INTERNAL_ERROR","An unexpected error occurred.")
