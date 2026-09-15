"""Loop-scoped connection pools with request-local headers and cookie jars."""

from __future__ import annotations

import asyncio
from weakref import WeakKeyDictionary
import httpx

_transports: WeakKeyDictionary = WeakKeyDictionary()


class _BorrowedTransport(httpx.AsyncBaseTransport):
    def __init__(self, transport: httpx.AsyncHTTPTransport):
        self.transport = transport

    async def handle_async_request(self, request):
        return await self.transport.handle_async_request(request)

    async def aclose(self):
        # The application lifespan owns the underlying pool.
        pass


def provider_http_client() -> httpx.AsyncClient:
    loop = asyncio.get_running_loop()
    transport = _transports.get(loop)
    if transport is None:
        transport = httpx.AsyncHTTPTransport(
            limits=httpx.Limits(
                max_connections=50, max_keepalive_connections=20, keepalive_expiry=30
            )
        )
        _transports[loop] = transport
    return httpx.AsyncClient(transport=_BorrowedTransport(transport), timeout=60)


async def close_provider_http_pool():
    transport = _transports.pop(asyncio.get_running_loop(), None)
    if transport is not None:
        await transport.aclose()
