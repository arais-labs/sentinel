from decimal import Decimal

import pytest

from sentral.llm.pricing import claude_token_price, openai_token_price
from sentral.llm.providers.codex import CodexProvider
from sentral.llm.providers.openai import OpenAIProvider


def usage(input_tokens=1000):
    return {
        "input_tokens": input_tokens,
        "input_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 100},
        "output_tokens": 100,
        "output_tokens_details": {"reasoning_tokens": 80},
        "total_tokens": input_tokens + 100,
    }


@pytest.mark.parametrize(
    "tier,multiplier", [("default", 1), ("fast", 2), ("priority", 2), ("flex", 0.5)]
)
def test_rates_cache_and_reasoning_are_not_double_counted(tier, multiplier):
    price = openai_token_price("gpt-5.6-sol", usage(), tier)
    # 700 uncached + 200 cached + 100 written; reasoning is already in output.
    assert Decimal(price["usd"]) == Decimal(".00538") * Decimal(str(multiplier))


def test_long_context_threshold_applies_to_entire_request():
    assert (
        openai_token_price("gpt-5.6-sol", usage(272000), "default")["rates_per_million"]["input"]
        == "4"
    )
    price = openai_token_price("gpt-5.6-sol", usage(272001), "default")
    assert price["rates_per_million"] == {
        "input": "8",
        "cached_input": "0.8",
        "cache_write": "10",
        "output": "30.0",
    }


def test_unknown_or_incomplete_usage_is_not_free():
    assert openai_token_price("unknown", usage(), "default") is None
    assert openai_token_price("gpt-5.6-sol", usage(), "unknown") is None
    assert openai_token_price("gpt-5.6-sol", {}, "default") is None
    assert openai_token_price("gpt-5.6-sol", usage(20), "default") is None
    incomplete = usage()
    del incomplete["input_tokens_details"]["cache_write_tokens"]
    assert openai_token_price("gpt-5.6-sol", incomplete, "default") is None
    zero = {
        "input_tokens": 0,
        "output_tokens": 0,
        "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
    }
    assert Decimal(openai_token_price("gpt-5.6-sol", zero, "default")["usd"]) == 0


@pytest.mark.parametrize("codex", [False, True])
def test_response_preserves_usage_and_distinguishes_subscription(codex):
    provider = CodexProvider(oauth_token="test") if codex else OpenAIProvider("test")
    response = {
        "id": "response-test",
        "model": "gpt-5.6-sol",
        "usage": usage(),
        "service_tier": "default",
    }
    message = provider._responses_message(response, "requested-alias", [])
    assert message.provider_usage["usage"] == response["usage"]
    assert message.provider_usage["price_kind"] == ("api_equivalent" if codex else "api_list_price")
    assert Decimal(message.provider_usage["price"]["usd"]) == Decimal(".00538")
    response["usage"]["input_tokens"] = 99
    assert message.provider_usage["usage"]["input_tokens"] == 1000
    assert provider._responses_message({}, "gpt-5.6-sol", []).provider_usage is None


def test_custom_endpoint_is_not_priced_as_openai():
    provider = OpenAIProvider("test", base_url="https://custom.example/v1")
    message = provider._responses_message(
        {"usage": usage(), "service_tier": "default"}, "gpt-5.6-sol", []
    )
    assert message.provider_usage["usage"] == usage()
    assert message.provider_usage["price"] is None


@pytest.mark.parametrize("model,cost", [("gpt-6.1-sol", ".00267"), ("gpt-6-luna", ".0001345")])
def test_current_openai_prices_cover_cache_reads_and_writes(model, cost):
    price = openai_token_price(model, usage(), "default")
    assert Decimal(price["usd"]) == Decimal(cost)
    assert price["rates_as_of"] == "2026-09-30"
    assert Decimal(openai_token_price(model, usage(), "fast")["usd"]) == Decimal(cost) * 2


@pytest.mark.parametrize(
    "model,cost", [("claude-opus-5-5", ".00694"), ("claude-sonnet-5-5", ".00349")]
)
def test_current_claude_prices_cover_both_cache_write_durations(model, cost):
    price = claude_token_price(
        model,
        {
            "input_tokens": 1000,
            "cache_read_input_tokens": 200,
            "cache_creation_input_tokens": 150,
            "cache_creation": {
                "ephemeral_5m_input_tokens": 100,
                "ephemeral_1h_input_tokens": 50,
            },
            "output_tokens": 100,
        },
    )
    assert Decimal(price["usd"]) == Decimal(cost)
    assert price["rates_as_of"] == "2026-09-30"
