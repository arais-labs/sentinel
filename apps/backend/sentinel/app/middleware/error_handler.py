from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def _status_to_code(status_code: int) -> str:
    mapping = {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        422: "validation_error",
        429: "rate_limited",
        500: "internal_error",
    }
    return mapping.get(status_code, "internal_error")


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _error_payload(*, code: str, message: str, details: dict | list | None = None) -> dict:
    payload = {"error": {"code": code, "message": message}}
    if details is not None:
        payload["error"]["details"] = details
    return payload


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        code = _status_to_code(exc.status_code)
        message = exc.detail if isinstance(exc.detail, str) else "Request failed"
        details = exc.detail if isinstance(exc.detail, (dict, list)) else None
        response = JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(code=code, message=message, details=details),
        )
        request_id = _request_id(request)
        if request_id:
            response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = _json_safe(exc.errors())
        response = JSONResponse(
            status_code=422,
            content=_error_payload(
                code="validation_error",
                message="Request validation failed",
                details=details,
            ),
        )
        request_id = _request_id(request)
        if request_id:
            response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, _: Exception) -> JSONResponse:
        response = JSONResponse(
            status_code=500,
            content=_error_payload(code="internal_error", message="Internal server error"),
        )
        request_id = _request_id(request)
        if request_id:
            response.headers["X-Request-ID"] = request_id
        return response
