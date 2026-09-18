"""Browser authorization and encrypted token storage for instance MCP servers."""

import asyncio
import secrets
from contextlib import suppress
from time import time
from urllib.parse import parse_qs, urlsplit

import httpx2
from mcp.client.auth import OAuthClientProvider
from mcp.client.auth.oauth2 import build_oauth_authorization_server_metadata_discovery_urls
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthMetadata,
    OAuthToken,
)

from app.models.mcp import MCPServer
from app.schemas.mcp import MCPConnection
from app.services.mcp.errors import SignInRequired, connection_message


class SentinelOAuthClientProvider(OAuthClientProvider):
    """Keep OAuth subrequests identifiable to strict provider gateways."""

    async def async_auth_flow(self, request):
        if self.context.oauth_metadata is None:
            client = await self.context.storage.get_client_info()
            issuer = str(client.issuer) if client and client.issuer else None
            if issuer:
                for url in build_oauth_authorization_server_metadata_discovery_urls(
                    issuer, self.context.server_url
                ):
                    metadata_request = httpx2.Request(
                        "GET",
                        url,
                        headers={
                            "User-Agent": "Sentinel/2.0 MCP",
                            "Accept": "application/json",
                        },
                    )
                    metadata_response = yield metadata_request
                    if metadata_response.status_code != 200:
                        continue
                    try:
                        metadata = OAuthMetadata.model_validate_json(
                            await metadata_response.aread()
                        )
                    except ValueError:
                        continue
                    if str(metadata.issuer).rstrip("/") != issuer.rstrip("/"):
                        continue
                    self.context.oauth_metadata = metadata
                    self.context.auth_server_url = issuer
                    await self.context.storage.set_oauth_metadata(metadata)
                    break
        flow = super().async_auth_flow(request)
        try:
            outgoing = await anext(flow)
            while True:
                # Some provider gateways reject SDK-created requests because they
                # bypass the HTTP client's normal default headers.
                outgoing.headers.setdefault("User-Agent", "Sentinel/2.0 MCP")
                outgoing.headers.setdefault("Accept", "application/json")
                response = yield outgoing
                if (
                    response.status_code == 200
                    and "/.well-known/oauth-" in outgoing.url.path
                    and hasattr(self.context.storage, "set_oauth_metadata")
                ):
                    try:
                        metadata = OAuthMetadata.model_validate_json(await response.aread())
                    except ValueError:
                        pass
                    else:
                        await self.context.storage.set_oauth_metadata(metadata)
                outgoing = await flow.asend(response)
        except StopAsyncIteration:
            return
        finally:
            await flow.aclose()


class TokenStore:
    def __init__(self, factory, server_id, url):
        self.factory, self.server_id, self.url = factory, server_id, url

    async def read(self):
        async with self.factory() as db:
            server = await db.get(MCPServer, self.server_id)
            if server is None:
                raise RuntimeError("MCP server removed")
            config = MCPConnection.model_validate_json(server.config)
            if config.url != self.url:
                raise RuntimeError("MCP connection changed")
            return config

    async def update(self, **values):
        async with self.factory() as db:
            server = await db.get(MCPServer, self.server_id)
            if server is None:
                raise RuntimeError("MCP server removed")
            config = MCPConnection.model_validate_json(server.config)
            if config.url != self.url:
                raise RuntimeError("MCP connection changed")
            config.oauth.update(values)
            server.config = config.model_dump_json()
            await db.commit()

    async def get_tokens(self):
        data = (await self.read()).oauth.get("tokens")
        return OAuthToken.model_validate(data) if data else None

    async def set_tokens(self, tokens):
        await self.update(tokens=tokens.model_dump(mode="json"), saved_at=time())

    async def get_client_info(self):
        data = (await self.read()).oauth.get("client")
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def set_client_info(self, client_info):
        await self.update(client=client_info.model_dump(mode="json"))

    async def set_oauth_metadata(self, metadata):
        await self.update(metadata=metadata.model_dump(mode="json"))


def provider(config, storage, redirect_uri=None, redirect_handler=None, callback_handler=None):
    async def sign_in_required(*args):
        raise SignInRequired("Sign in from MCP settings")

    saved = config.oauth.get("client") or {}
    redirect_uris = saved.get("redirect_uris") or ["http://127.0.0.1/callback"]
    auth = SentinelOAuthClientProvider(
        server_url=config.url,
        storage=storage,
        client_metadata=OAuthClientMetadata(
            client_name="Sentinel",
            redirect_uris=[redirect_uri or redirect_uris[0]],
            token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"],
        ),
        redirect_handler=redirect_handler or sign_in_required,
        callback_handler=callback_handler or sign_in_required,
    )
    metadata = config.oauth.get("metadata")
    if metadata:
        auth.context.oauth_metadata = OAuthMetadata.model_validate(metadata)
        auth.context.auth_server_url = str(auth.context.oauth_metadata.issuer)
    tokens = config.oauth.get("tokens") or {}
    if tokens.get("expires_in") is not None and config.oauth.get("saved_at") is not None:
        auth.context.token_expiry_time = config.oauth["saved_at"] + tokens["expires_in"]
    return auth


class Login:
    def __init__(self, factory, server_id, completed):
        self.factory, self.server_id, self.completed = factory, server_id, completed
        self.status = "connecting"
        self.authorization_url = None
        self.message = None
        self.expected_state = None
        self.callback_path = "/callback/" + secrets.token_urlsafe(24)
        self.code = asyncio.get_running_loop().create_future()
        self.task = asyncio.create_task(self.run())

    def public(self):
        return {
            "status": self.status,
            "authorization_url": self.authorization_url,
            "message": self.message,
        }

    async def redirect(self, url):
        if urlsplit(url).scheme not in {"https", "http"}:
            raise RuntimeError("Unsupported sign-in URL")
        self.expected_state = parse_qs(urlsplit(url).query).get("state", [""])[0]
        self.authorization_url = url
        self.status = "sign_in"

    async def callback(self):
        return await self.code

    async def receive(self, reader, writer):
        status, text = (
            "400 Bad Request",
            "Invalid sign-in response. Return to Sentinel and try again.",
        )
        try:
            async with asyncio.timeout(10):
                request = await reader.readuntil(b"\r\n\r\n")
            line = request.split(b"\r\n", 1)[0].decode("ascii")
            method, target, _ = line.split(" ", 2)
            parsed = urlsplit(target)
            params = parse_qs(parsed.query)
            state = params.get("state", [""])[0]
            if (
                method == "GET"
                and parsed.path == self.callback_path
                and self.expected_state
                and secrets.compare_digest(state, self.expected_state)
                and not self.code.done()
            ):
                if params.get("error"):
                    self.code.set_exception(SignInRequired("Sign-in was cancelled."))
                    text = "Sign-in cancelled. You can close this tab and return to Sentinel."
                elif params.get("code"):
                    self.code.set_result(
                        AuthorizationCodeResult(
                            code=params["code"][0], state=state, iss=params.get("iss", [None])[0]
                        )
                    )
                    text = "Return to Sentinel to finish connecting. You can close this tab."
                    status = "200 OK"
        except (ValueError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass
        html = (
            '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<title>Sentinel</title><body style="margin:0;background:#0c0c0e;color:#eee;font:16px system-ui;display:grid;place-content:center;min-height:100vh">'
            '<main style="max-width:440px;padding:32px"><h1 style="font-size:24px">Sentinel</h1><p>'
            + text
            + "</p></main>"
        ).encode()
        writer.write(
            f"HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\nCache-Control: no-store\r\nContent-Security-Policy: default-src 'none'; style-src 'unsafe-inline'\r\nContent-Length: {len(html)}\r\nConnection: close\r\n\r\n".encode()
            + html
        )
        with suppress(ConnectionError):
            await writer.drain()
        writer.close()
        with suppress(ConnectionError):
            await writer.wait_closed()

    async def run(self):
        try:
            async with asyncio.timeout(300):
                async with self.factory() as db:
                    server = await db.get(MCPServer, self.server_id)
                    config = MCPConnection.model_validate_json(server.config)
                if config.transport == "stdio":
                    tools = await self.completed(config, None, None)
                else:
                    # Settings always detects the remote transport, including connections
                    # saved with the old manual SSE selector.
                    config.transport = "auto"
                    store = TokenStore(self.factory, self.server_id, config.url)
                    saved = await store.get_client_info()
                    previous = (
                        urlsplit(str(saved.redirect_uris[0]))
                        if saved and saved.redirect_uris
                        else None
                    )
                    port = previous.port if previous and previous.hostname == "127.0.0.1" else 0
                    if previous and port:
                        self.callback_path = previous.path
                    try:
                        listener = await asyncio.start_server(
                            self.receive, "127.0.0.1", port or 0, limit=8192
                        )
                    except OSError:
                        listener = await asyncio.start_server(
                            self.receive, "127.0.0.1", 0, limit=8192
                        )
                        await store.update(client=None)
                    async with listener:
                        port = listener.sockets[0].getsockname()[1]
                        redirect_uri = f"http://127.0.0.1:{port}{self.callback_path}"
                        auth = (
                            None
                            if any(k.lower() == "authorization" for k in config.headers)
                            else provider(config, store, redirect_uri, self.redirect, self.callback)
                        )
                        tools = await self.completed(config, auth, store)
                self.status = "connected"
                self.message = f"{len(tools)} tools available"
        except asyncio.CancelledError:
            self.status = "cancelled"
            raise
        except Exception as error:
            self.status = "error"
            self.message = connection_message(error)
        finally:
            self.authorization_url = None
            if not self.code.done():
                self.code.cancel()


_logins = {}


def get(factory, server_id):
    return _logins.get((factory, server_id))


async def cancel(factory, server_id=None):
    for key, login in list(_logins.items()):
        if key[0] is factory and (server_id is None or key[1] == server_id):
            _logins.pop(key, None)
            login.task.cancel()
            with suppress(asyncio.CancelledError):
                await login.task


async def start(factory, server_id, completed):
    await cancel(factory, server_id)
    login = Login(factory, server_id, completed)
    _logins[(factory, server_id)] = login
    return login
