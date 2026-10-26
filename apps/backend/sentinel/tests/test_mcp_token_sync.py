"""Long-lived MCP sessions must use the newest stored OAuth tokens (providers rotate refresh tokens)."""

import json
from time import time
from urllib.parse import parse_qs

import httpx2
import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from app.schemas.mcp import MCPConnection
from app.services.mcp import oauth

URL = "https://mcp.example.com/mcp"
CLIENT = {"client_id": "sentinel-test", "redirect_uris": ["http://127.0.0.1/callback"]}


class Store:
    def __init__(self, tokens, saved_at):
        self.oauth = {"tokens": tokens, "saved_at": saved_at, "client": CLIENT}

    async def read(self):
        return MCPConnection(url=URL, oauth=self.oauth)

    async def get_tokens(self):
        return OAuthToken.model_validate(self.oauth["tokens"])

    async def set_tokens(self, tokens):
        self.oauth.update(tokens=tokens.model_dump(mode="json"), saved_at=time())

    async def get_client_info(self):
        return OAuthClientInformationFull.model_validate(CLIENT)

    async def set_client_info(self, info):
        pass

    async def set_oauth_metadata(self, metadata):
        pass


def token(access, refresh):
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": 3600,
        "refresh_token": refresh,
    }


def provider(store):
    return oauth.provider(MCPConnection(url=URL, oauth=store.oauth), store)


@pytest.mark.asyncio
async def test_session_adopts_tokens_another_client_rotated():
    store = Store(token("old", "r-old"), time())
    auth = provider(store)
    flow = auth.async_auth_flow(httpx2.Request("POST", URL))
    assert (await anext(flow)).headers["authorization"] == "Bearer old"
    await flow.aclose()
    # Another client (the settings check, another session) refreshed and rotated the tokens.
    store.oauth.update(tokens=token("new", "r-new"), saved_at=time())
    flow = auth.async_auth_flow(httpx2.Request("POST", URL))
    request = await anext(flow)
    assert request.headers["authorization"] == "Bearer new"
    await flow.aclose()


@pytest.mark.asyncio
async def test_expired_stored_token_is_refreshed_not_a_sign_in():
    store = Store(token("stale", "r1"), time() - 7200)  # saved two hours ago, lasts one
    auth = provider(store)
    flow = auth.async_auth_flow(httpx2.Request("POST", URL))
    refresh = await anext(flow)
    assert refresh.url.path == "/token"
    assert parse_qs(refresh.content.decode())["refresh_token"] == ["r1"]
    response = httpx2.Response(
        200, content=json.dumps(token("fresh", "r2")).encode(), request=refresh
    )
    request = await flow.asend(response)
    assert request.headers["authorization"] == "Bearer fresh"
    assert store.oauth["tokens"]["refresh_token"] == "r2"
    await flow.aclose()
