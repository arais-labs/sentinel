"""Native module: http_request — outbound HTTP requests."""

from __future__ import annotations

import ipaddress
import os
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

from app.services.tools.executor import ToolValidationError

# ---------------------------------------------------------------------------
# Constants (moved from builtin.py)
# ---------------------------------------------------------------------------

_MAX_HTTP_RESPONSE_BYTES = 1_048_576

# ---------------------------------------------------------------------------
# Internal helpers (moved from builtin.py)
# ---------------------------------------------------------------------------


async def _validate_public_hostname(hostname: str) -> None:
    """Raise ToolValidationError if hostname resolves to a private/reserved IP."""
    normalized_hostname = hostname.strip().lower().rstrip(".")
    allowed_hosts_raw = os.environ.get("SSRF_ALLOW_HOSTS", "")
    allowed_hosts = {
        value.strip().lower().rstrip(".") for value in allowed_hosts_raw.split(",") if value.strip()
    }
    if normalized_hostname in allowed_hosts:
        return
    if os.environ.get("SSRF_ALLOW_PRIVATE", "").lower() in ("1", "true", "yes"):
        return
    try:
        addr_info = socket.getaddrinfo(normalized_hostname, None)
    except socket.gaierror as exc:
        raise ToolValidationError(f"Cannot resolve hostname: {normalized_hostname}") from exc

    blocked: list[str] = []
    for item in addr_info:
        ip_text = item[4][0]
        ip_addr = ipaddress.ip_address(ip_text)
        if (
            ip_addr.is_private
            or ip_addr.is_loopback
            or ip_addr.is_link_local
            or ip_addr.is_reserved
            or ip_addr.is_multicast
            or ip_addr.is_unspecified
        ):
            blocked.append(ip_text)

    if blocked:
        raise ToolValidationError(
            f"SSRF blocked: {normalized_hostname} resolves to private/internal address {', '.join(sorted(set(blocked)))}"
        )


# ---------------------------------------------------------------------------
# Handler functions (module-level)
# ---------------------------------------------------------------------------


async def handle_request(payload: dict[str, Any]) -> dict[str, Any]:
    url = payload.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ToolValidationError("Field 'url' must be a non-empty string")
    parsed_url = urlparse(url.strip())
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
        raise ToolValidationError("Field 'url' must be a valid http/https URL")
    await _validate_public_hostname(parsed_url.hostname)
    method = payload.get("method", "GET")
    if not isinstance(method, str):
        raise ToolValidationError("Field 'method' must be a string")
    method = method.upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise ToolValidationError("Unsupported HTTP method")

    timeout_seconds = payload.get("timeout_seconds", 10)
    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or timeout_seconds <= 0
    ):
        raise ToolValidationError("Field 'timeout_seconds' must be a positive integer")

    headers = payload.get("headers", {})
    if headers is None:
        headers = {}
    if not isinstance(headers, dict):
        raise ToolValidationError("Field 'headers' must be an object")

    request_headers = {str(k): str(v) for k, v in headers.items()}
    request_kwargs: dict[str, Any] = {"headers": request_headers}
    if "body" in payload:
        body = payload["body"]
        if isinstance(body, (dict, list)):
            request_kwargs["json"] = body
        else:
            request_kwargs["content"] = str(body)

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.request(method, url, **request_kwargs)

    content_type = response.headers.get("content-type", "")
    response_bytes = response.content
    truncated = len(response_bytes) > _MAX_HTTP_RESPONSE_BYTES
    visible_bytes = response_bytes[:_MAX_HTTP_RESPONSE_BYTES]

    if "application/json" in content_type and not truncated:
        try:
            parsed_body: Any = response.json()
        except ValueError:
            parsed_body = response.text
    else:
        parsed_body = visible_bytes.decode("utf-8", errors="replace")
        if truncated:
            parsed_body += "\n... [truncated - response exceeded 1 MB]"

    return {
        "status_code": response.status_code,
        "headers": dict(response.headers),
        "body": parsed_body,
        "truncated": truncated,
    }

