import asyncio
import base64
import hashlib
import socket
from time import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import uvicorn
from mcp.server import MCPServer as ProtocolServer
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.models.base import Base
from app.models.mcp import MCPServer
from app.schemas.mcp import MCPConnection
from app.services.mcp import client, oauth


@pytest.mark.asyncio
async def test_browser_oauth_pkce_callback_storage_and_refresh(tmp_path):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "oauth.db"}')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    base = f"http://127.0.0.1:{sock.getsockname()[1]}"
    counts = {"register": 0, "refresh": 0, "wrong_refresh": 0}
    authorization = {}

    async def resource(request):
        assert request.headers["user-agent"] == "Sentinel/2.0 MCP"
        return JSONResponse(
            {
                "resource": base + "/mcp",
                "authorization_servers": [base + "/"],
                "scopes_supported": ["read", "offline_access"],
            }
        )

    async def metadata(request):
        assert request.headers["user-agent"] == "Sentinel/2.0 MCP"
        return JSONResponse(
            {
                "issuer": base + "/",
                "authorization_endpoint": base + "/authorize",
                "token_endpoint": base + "/oauth2/api/v1/token",
                "registration_endpoint": base + "/register",
                "response_types_supported": ["code"],
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "scopes_supported": ["read", "offline_access"],
            }
        )

    async def register(request):
        assert request.headers["user-agent"] == "Sentinel/2.0 MCP"
        counts["register"] += 1
        data = await request.json()
        assert data["client_name"] == "Sentinel"
        assert data["token_endpoint_auth_method"] == "none"
        return JSONResponse({**data, "client_id": "test-client"}, status_code=201)

    async def token(request):
        assert request.headers["user-agent"] == "Sentinel/2.0 MCP"
        data = parse_qs((await request.body()).decode())
        if data["grant_type"] == ["authorization_code"]:
            assert data["code"] == ["test-code"]
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(data["code_verifier"][0].encode()).digest())
                .rstrip(b"=")
                .decode()
            )
            assert challenge == authorization["code_challenge"][0]
        else:
            assert data["grant_type"] == ["refresh_token"]
            assert data["refresh_token"] == ["test-refresh"]
            counts["refresh"] += 1
        return JSONResponse(
            {
                "access_token": "test-access",
                "refresh_token": "test-refresh",
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": "read offline_access",
            }
        )

    async def wrong_token(request):
        counts["wrong_refresh"] += 1
        return JSONResponse({"error": "wrong token endpoint"}, status_code=404)

    protocol = ProtocolServer("OAuth test")

    @protocol.tool()
    def echo(value: str) -> str:
        return value

    app = protocol.streamable_http_app()
    app.routes.extend(
        [
            Route("/.well-known/oauth-protected-resource", resource),
            Route("/.well-known/oauth-authorization-server", metadata),
            Route("/register", register, methods=["POST"]),
            Route("/oauth2/api/v1/token", token, methods=["POST"]),
            Route("/token", wrong_token, methods=["POST"]),
        ]
    )

    async def protected(scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/mcp"):
            headers = dict(scope["headers"])
            if headers.get(b"authorization") != b"Bearer test-access":
                response = JSONResponse(
                    {"error": "sign_in_required"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
                await response(scope, receive, send)
                return
        await app(scope, receive, send)

    web = uvicorn.Server(uvicorn.Config(protected, log_level="error", lifespan="on"))
    task = asyncio.create_task(web.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not web.started:
                await asyncio.sleep(0.01)
        config = MCPConnection(url=base + "/mcp")
        async with factory() as db:
            db.add(
                MCPServer(
                    id="oauth-server",
                    name="OAuth server",
                    config=config.model_dump_json(),
                    tools=[],
                    enabled=False,
                )
            )
            await db.commit()

        async def complete(config, auth, store):
            return await client.discover(config, auth)

        login = await oauth.start(factory, "oauth-server", complete)
        async with asyncio.timeout(10):
            while login.status == "connecting":
                await asyncio.sleep(0.01)
        assert login.status == "sign_in", login.message
        authorization.update(parse_qs(urlsplit(login.authorization_url).query))
        assert authorization["code_challenge_method"] == ["S256"]
        assert set(authorization["scope"][0].split()) == {"read", "offline_access"}
        redirect = authorization["redirect_uri"][0]
        async with httpx.AsyncClient() as browser:
            wrong = await browser.get(redirect, params={"state": "wrong", "code": "test-code"})
            assert wrong.status_code == 400
            assert not login.code.done()
            valid = await browser.get(
                redirect, params={"state": authorization["state"][0], "code": "test-code"}
            )
            assert valid.status_code == 200
        await asyncio.wait_for(asyncio.shield(login.task), 10)
        assert login.status == "connected", login.message
        store = oauth.TokenStore(factory, "oauth-server", config.url)
        saved = await store.read()
        assert saved.oauth["tokens"]["refresh_token"] == "test-refresh"
        assert saved.oauth["metadata"]["token_endpoint"] == base + "/oauth2/api/v1/token"
        assert counts["register"] == 1
        await store.update(saved_at=time() - 7200)
        saved = await store.read()
        discovered = await client.discover(saved, oauth.provider(saved, store))
        assert discovered[0]["name"] == "echo"
        assert counts["refresh"] == 1
        assert counts["wrong_refresh"] == 0
        assert counts["register"] == 1

        # Connections created before Sentinel persisted discovery metadata must
        # recover it from the registered client's issuer before refreshing.
        await store.update(metadata=None, saved_at=time() - 7200)
        saved = await store.read()
        discovered = await client.discover(saved, oauth.provider(saved, store))
        assert discovered[0]["name"] == "echo"
        assert counts["refresh"] == 2
        assert counts["wrong_refresh"] == 0
    finally:
        await oauth.cancel(factory)
        web.should_exit = True
        await task
        sock.close()
        await engine.dispose()
