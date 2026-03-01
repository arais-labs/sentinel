"""Shared error classification helpers for provider routing/retry logic."""

from __future__ import annotations

import httpx


class TransientProviderError(RuntimeError):
    """Provider-specific transient failure that should be retried."""


def status_code(exc: Exception) -> int | None:
    """Best-effort extraction of HTTP status code from provider exceptions."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code
    return getattr(exc, "status_code", None)


def is_retryable(exc: Exception) -> bool:
    """Classify whether an exception is safe to retry automatically."""
    if isinstance(exc, TransientProviderError):
        return True
    if isinstance(exc, (TimeoutError, ConnectionError, httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError)):
        return True
    code = status_code(exc)
    if code is None:
        return False
    if code == 429:
        return True
    return code >= 500


def error_tag(exc: Exception) -> str:
    """Return a compact diagnostic tag for logs and aggregate error messages."""
    if isinstance(exc, TransientProviderError):
        return "transient_provider_error"
    code = status_code(exc)
    if code == 429:
        return "rate_limited"
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "timeout"
    if isinstance(exc, (ConnectionError, httpx.ConnectError, httpx.NetworkError)):
        return "connection_error"
    if code is not None:
        body = ""
        if isinstance(exc, httpx.HTTPStatusError):
            # Streaming responses can raise ResponseNotRead when .text is
            # accessed before the body has been read.
            try:
                body = (exc.response.text or "")[:300]
            except Exception:  # noqa: BLE001
                body = "<streaming body unavailable>"
        return f"http_{code}: {body}".strip()
    return f"{exc.__class__.__name__}: {exc}"
