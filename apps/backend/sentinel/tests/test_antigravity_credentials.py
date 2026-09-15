import base64
import json
from unittest.mock import AsyncMock

import pytest

from sentral.llm import antigravity_credentials as local
from sentral.llm.providers.gemini_oauth import GeminiOAuthCredentials, GeminiOAuthProvider


@pytest.mark.asyncio
@pytest.mark.parametrize("encoded", [True, False])
async def test_reads_scoped_keychain_entry(monkeypatch, encoded):
    raw = json.dumps(
        {
            "auth_method": "consumer",
            "token": {
                "access_token": "access",
                "refresh_token": "refresh",
                "expiry": "2026-09-15T10:00:00Z",
                "token_type": "Bearer",
            },
        }
    )
    if encoded:
        raw = "go-keyring-base64:" + base64.b64encode(raw.encode()).decode()
    process = AsyncMock()
    process.returncode = 0
    process.communicate.return_value = (raw.encode(), b"")
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(local.sys, "platform", "darwin")
    monkeypatch.setattr(local.asyncio, "create_subprocess_exec", spawn)
    creds = await local.read_antigravity_credentials()
    assert creds.refresh_token == "refresh"
    assert creds.expiry_date == 1789466400000
    assert creds.client_id.startswith("1071006060591-")
    assert spawn.call_args.args == (
        "security",
        "find-generic-password",
        "-s",
        "gemini",
        "-a",
        "antigravity",
        "-w",
    )


@pytest.mark.asyncio
async def test_missing_keychain_login(monkeypatch):
    process = AsyncMock()
    process.returncode = 44
    process.communicate.return_value = (b"", b"")
    monkeypatch.setattr(local.sys, "platform", "darwin")
    monkeypatch.setattr(local.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    assert await local.read_antigravity_credentials() is None


def test_legacy_client_cannot_be_silently_migrated():
    with pytest.raises(ValueError, match="belong to Gemini CLI"):
        GeminiOAuthCredentials.parse_input(
            {
                "refresh_token": "refresh",
                "client_id": "681255809395-legacy",
                "client_secret": "legacy-secret",
            }
        )


@pytest.mark.parametrize("method", ["gcp", "unknown"])
def test_rejects_non_consumer_profiles(method):
    with pytest.raises(ValueError, match="consumer OAuth"):
        GeminiOAuthCredentials.parse_input({"auth_method": method, "token": {"refresh_token": "r"}})


@pytest.mark.asyncio
async def test_antigravity_request_protocol():
    provider = GeminiOAuthProvider({"access_token": "access", "refresh_token": "refresh"})
    provider._project_id = "test-project"
    request = await provider._build_code_assist_request("gemini-3.8-flash-tiered", {"contents": []})
    headers = await provider._request_headers()
    assert provider._base_url == "https://daily-cloudcode-pa.googleapis.com/v1internal"
    assert request["model"] == "gemini-3.8-flash-tiered"
    assert request["userAgent"] == "antigravity"
    assert request["requestId"].startswith("sentinel-")
    assert headers["authorization"] == "Bearer access"
    assert headers["user-agent"].startswith("antigravity-cli/")
    assert "x-goog-api-key" not in headers


@pytest.mark.asyncio
async def test_concurrent_requests_refresh_once(monkeypatch):
    import asyncio

    provider = GeminiOAuthProvider({"refresh_token": "refresh"})
    calls = []

    async def refresh():
        calls.append(True)
        await asyncio.sleep(0)
        provider._credentials.access_token = "new-token"

    monkeypatch.setattr(provider, "_refresh_access_token", refresh)
    assert (
        await asyncio.gather(*[provider._ensure_access_token() for _ in range(5)])
        == ["new-token"] * 5
    )
    assert len(calls) == 1
