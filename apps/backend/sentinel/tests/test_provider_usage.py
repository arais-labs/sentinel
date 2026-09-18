import asyncio
import base64
import json
from datetime import UTC, datetime

import httpx
import pytest

from app.config import Settings
from app.services.settings.provider_usage import ProviderUsageService, normalize_usage


def configured(**credentials):
    # Production hydrates these DB-only fields after constructing Settings.
    return Settings().model_copy(update=credentials)


def test_anthropic_weekly_and_session_remaining():
    windows = normalize_usage(
        "anthropic",
        {
            "seven_day": {"utilization": 41, "resets_at": "2026-09-23T04:59:59Z"},
            "five_hour": {"utilization": 100, "resets_at": None},
            "seven_day_sonnet": None,
            "seven_day_opus": {"utilization": 0},
        },
    )
    assert [(w.label, w.remaining_percent) for w in windows] == [
        ("Weekly", 59),
        ("5 hours", 0),
        ("Opus · Weekly", 100),
    ]
    assert windows[0].resets_at == datetime(2026, 9, 23, 4, 59, 59, tzinfo=UTC)
    assert windows[1].resets_at is None


def test_claude_named_fable_limit_overrides_legacy_without_duplicate_windows():
    windows = normalize_usage(
        "anthropic",
        {
            "seven_day": {"utilization": 40},
            "five_hour": {"utilization": 100},
            "nimbus_quill": {"utilization": 0},
            "limits": [
                {"kind": "session", "percent": 100, "is_active": True},
                {"kind": "weekly_all", "percent": 41},
                {
                    "kind": "weekly_scoped",
                    "percent": 71,
                    "is_active": False,
                    "scope": {"model": {"display_name": "Fable"}},
                    "resets_at": "2026-09-23T05:00:00Z",
                },
                {"kind": "weekly_scoped", "percent": 10, "scope": None},
            ],
        },
    )
    assert [(w.label, w.remaining_percent) for w in windows] == [
        ("Weekly", 59),
        ("5 hours", 0),
        ("Fable · Weekly", 29),
    ]
    assert windows[2].resets_at == datetime(2026, 9, 23, 5, tzinfo=UTC)


def test_codex_weekly_can_be_primary_and_reserve_is_separate():
    windows = normalize_usage(
        "openai",
        {
            "rate_limit": {
                "primary_window": {
                    "used_percent": 27,
                    "limit_window_seconds": 604800,
                    "reset_at": 1790318328,
                }
            },
            "additional_rate_limits": [
                {
                    "limit_name": "gpt-reserve",
                    "normal_model_slug": "gpt-5.6-luna",
                    "rate_limit": {
                        "primary_window": {"used_percent": 0, "limit_window_seconds": 604800},
                    },
                }
            ],
        },
    )
    assert [(w.label, w.remaining_percent) for w in windows] == [
        ("Weekly", 73),
        ("GPT-5.6 Luna reserve · Weekly", 100),
    ]
    assert windows[0].resets_at == datetime.fromtimestamp(1790318328, UTC)


def test_codex_secondary_weekly_and_unknown_duration():
    windows = normalize_usage(
        "openai",
        {
            "rate_limit": {
                "primary_window": {"used_percent": 10, "limit_window_seconds": 18000},
                "secondary_window": {"used_percent": 50, "limit_window_seconds": 604800},
            }
        },
    )
    assert [w.label for w in windows] == ["5 hours", "Weekly"]
    assert (
        normalize_usage("openai", {"rate_limit": {"primary_window": {"used_percent": 0}}})[0].label
        == "Usage limit"
    )


def test_google_quota_is_not_mislabeled_as_weekly():
    windows = normalize_usage(
        "gemini",
        {
            "buckets": [
                {
                    "modelId": "gemini-pro",
                    "remainingFraction": 0.75,
                    "resetTime": "2026-09-23T04:59:59Z",
                },
                {"modelId": "gemini-flash", "remainingFraction": 0},
                {"modelId": "unknown"},
            ]
        },
    )
    assert [(w.label, w.remaining_percent) for w in windows] == [
        ("gemini-pro", 75),
        ("gemini-flash", 0),
    ]


@pytest.mark.parametrize("value", [None, "42", True, float("nan"), float("inf"), -1, 101, 10**1000])
def test_missing_or_invalid_usage_is_not_reported_as_unused(value):
    assert normalize_usage("anthropic", {"seven_day": {"utilization": value}}) == []


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
@pytest.mark.parametrize(
    "data", [None, [], {}, {"buckets": None}, {"additional_rate_limits": "bad"}]
)
def test_unavailable_schema_is_safe(provider, data):
    assert normalize_usage(provider, data) == []


@pytest.mark.asyncio
async def test_api_keys_do_not_trigger_requests():
    def reject(_request):
        raise AssertionError("API key must not be sent to subscription usage endpoint")

    service = ProviderUsageService(
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(reject))
    )
    settings = configured(
        anthropic_api_key="secret", openai_api_key="secret", gemini_api_key="secret"
    )
    for provider in ("anthropic", "openai", "gemini"):
        assert (await service.get_usage(provider, settings)).status == "not_connected"


@pytest.mark.asyncio
async def test_oauth_cache_coalesces_requests_and_isolates_accounts():
    calls = []

    async def handle(request):
        calls.append(request)
        await asyncio.sleep(0)
        return httpx.Response(200, json={"seven_day": {"utilization": 20}})

    service = ProviderUsageService(
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle))
    )
    settings = configured(anthropic_oauth_token="sk-ant-oat-account-one")
    first, second = await asyncio.gather(
        service.get_usage("anthropic", settings), service.get_usage("anthropic", settings)
    )
    assert first == second
    assert len(calls) == 1
    assert str(calls[0].url) == "https://api.anthropic.com/api/oauth/usage"
    assert calls[0].headers["authorization"] == "Bearer sk-ant-oat-account-one"
    assert "oauth-2025-04-20" in calls[0].headers["anthropic-beta"]
    first.windows.clear()
    assert (await service.get_usage("anthropic", settings)).windows
    await service.get_usage("anthropic", configured(anthropic_oauth_token="sk-ant-oat-account-two"))
    assert len(calls) == 2
    # Expiry permits a new request; cache keys never contain the credential itself.
    for key, (_, result) in list(service._cache.items()):
        assert "sk-ant" not in repr(key)
        service._cache[key] = (0, result)
    await service.get_usage("anthropic", settings)
    assert len(calls) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429, 500])
async def test_http_errors_are_sanitized_and_cached(status):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, text="private upstream details secret-token")

    service = ProviderUsageService(
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle))
    )
    settings = configured(openai_oauth_token="secret-token")
    result = await service.get_usage("openai", settings)
    assert result.status == "unavailable"
    assert "secret-token" not in result.model_dump_json()
    assert "private" not in result.model_dump_json()
    assert result.windows == []
    await service.get_usage("openai", settings)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_codex_account_header_and_usage_endpoint():
    claims = (
        base64.urlsafe_b64encode(
            json.dumps(
                {"https://api.openai.com/auth": {"chatgpt_account_id": "test-account"}}
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    token = f"header.{claims}.signature"

    def handle(request):
        assert request.method == "GET"
        assert str(request.url) == "https://chatgpt.com/backend-api/wham/usage"
        assert request.headers["ChatGPT-Account-ID"] == "test-account"
        assert request.headers["authorization"] == f"Bearer {token}"
        return httpx.Response(
            200,
            json={
                "rate_limit": {
                    "primary_window": {"used_percent": 27, "limit_window_seconds": 604800}
                }
            },
        )

    service = ProviderUsageService(
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle))
    )
    assert (await service.get_usage("openai", configured(openai_oauth_token=token))).windows[
        0
    ].remaining_percent == 73


@pytest.mark.asyncio
async def test_gemini_reuses_project_lookup_and_oauth_headers():
    calls = []

    def handle(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer google-token"
        if request.url.path.endswith(":loadCodeAssist"):
            return httpx.Response(200, json={"cloudaicompanionProject": "test-project"})
        assert (
            str(request.url)
            == "https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota"
        )
        assert json.loads(request.content) == {"project": "test-project"}
        assert request.headers["accept"] == "application/json"
        return httpx.Response(
            200, json={"buckets": [{"modelId": "gemini-pro", "remainingFraction": 0.5}]}
        )

    service = ProviderUsageService(
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle))
    )
    credentials = json.dumps({"access_token": "google-token", "refresh_token": "refresh-token"})
    result = await service.get_usage("gemini", configured(gemini_oauth_credentials=credentials))
    assert result.status == "available"
    assert result.windows[0].remaining_percent == 50
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_router_uses_current_instance_oauth_settings(monkeypatch):
    from app.routers import settings as router

    sentinel = configured(openai_oauth_token="instance-token")

    class SettingsStub:
        async def build_instance_settings(self, db):
            assert db == "instance-db"
            return sentinel

    class UsageStub:
        async def get_usage(self, provider, settings):
            assert provider == "openai"
            assert settings is sentinel
            return "usage-result"

    monkeypatch.setattr(router, "provider_usage_service", UsageStub())
    assert (
        await router.get_provider_usage("openai", "instance-db", SettingsStub()) == "usage-result"
    )
