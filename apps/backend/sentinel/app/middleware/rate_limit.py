from __future__ import annotations

import asyncio
import math
import re
import time
from collections import defaultdict, deque

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class RateLimitMiddleware(BaseHTTPMiddleware):
    WINDOW_SECONDS = 60
    CLEANUP_INTERVAL_SECONDS = 30

    # Route-level limits from API contract (v1 subset currently implemented in code)
    _RULES: list[tuple[str, re.Pattern[str], int, str]] = [
        ("POST", re.compile(r"^/api/v1/auth/login$"), 10, "public_ip"),
        ("POST", re.compile(r"^/api/v1/auth/refresh$"), 30, "token_or_ip"),
        ("POST", re.compile(r"^/api/v1/auth/change-password$"), 10, "token_or_ip"),
        ("DELETE", re.compile(r"^/api/v1/auth/session$"), 30, "token_or_ip"),
        ("GET", re.compile(r"^/api/v1/sessions$"), 60, "token_or_ip"),
        ("POST", re.compile(r"^/api/v1/sessions$"), 20, "token_or_ip"),
        ("POST", re.compile(r"^/api/v1/sessions/[^/]+/stop$"), 30, "token_or_ip"),
        ("GET", re.compile(r"^/api/v1/memory$"), 60, "token_or_ip"),
        ("POST", re.compile(r"^/api/v1/sessions/[^/]+/sub-agents$"), 10, "token_or_ip"),
        ("GET", re.compile(r"^/api/v1/triggers$"), 60, "token_or_ip"),
        ("GET", re.compile(r"^/api/v1/tools$"), 60, "token_or_ip"),
        ("POST", re.compile(r"^/api/v1/admin/estop$"), 5, "token_or_ip"),
        ("DELETE", re.compile(r"^/api/v1/admin/estop$"), 5, "token_or_ip"),
        ("POST", re.compile(r"^/api/v1/webhooks/[^/]+$"), 60, "public_ip"),
    ]

    _buckets: dict[str, deque[float]] = defaultdict(deque)
    _lock = asyncio.Lock()

    @classmethod
    async def cleanup_expired(cls) -> None:
        now = time.time()
        async with cls._lock:
            keys_to_delete: list[str] = []
            for bucket_key, bucket in cls._buckets.items():
                while bucket and now - bucket[0] >= cls.WINDOW_SECONDS:
                    bucket.popleft()
                if not bucket:
                    keys_to_delete.append(bucket_key)
            for key in keys_to_delete:
                cls._buckets.pop(key, None)

    @classmethod
    async def cleanup_loop(cls, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            await cls.cleanup_expired()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=cls.CLEANUP_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                continue

    def _match_rule(self, method: str, path: str) -> tuple[int, str] | None:
        for rule_method, pattern, limit, scope in self._RULES:
            if rule_method == method and pattern.match(path):
                return limit, scope
        return None

    @staticmethod
    def _client_ip(request: Request) -> str:
        fwd = request.headers.get("X-Forwarded-For")
        if fwd:
            return fwd.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    @staticmethod
    def _bearer_token(request: Request) -> str | None:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.lower().startswith("bearer "):
            return auth_header.split(" ", 1)[1].strip()
        cookie_token = request.cookies.get("sentinel_access_token")
        if cookie_token:
            return cookie_token.strip()
        return None

    async def dispatch(self, request: Request, call_next) -> Response:
        rule = self._match_rule(request.method, request.url.path)
        if not rule:
            return await call_next(request)

        limit, scope = rule
        if scope == "public_ip":
            identity = self._client_ip(request)
        else:
            identity = self._bearer_token(request) or self._client_ip(request)

        bucket_key = f"{request.method}:{request.url.path}:{identity}"
        now = time.time()

        async with self._lock:
            bucket = self._buckets[bucket_key]
            while bucket and now - bucket[0] >= self.WINDOW_SECONDS:
                bucket.popleft()

            if len(bucket) >= limit:
                retry_after = max(1, math.ceil(self.WINDOW_SECONDS - (now - bucket[0])))
                request_id = getattr(request.state, "request_id", None)
                content = {"error": {"code": "rate_limited", "message": "Rate limit exceeded"}}
                response = JSONResponse(status_code=429, content=content)
                response.headers["Retry-After"] = str(retry_after)
                if request_id:
                    response.headers["X-Request-ID"] = request_id
                return response

            bucket.append(now)

        return await call_next(request)
