from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from app.services.llm.live_credentials import LiveCredentialProvider
from sentral.llm.generic.base import LLMProvider
from sentral.llm.generic.types import AssistantMessage


class _Provider(LLMProvider):
    def __init__(self, credential: str) -> None:
        self.credential = credential
        self.closed = False

    @property
    def name(self) -> str:
        return "test"

    async def chat(
        self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None
    ):
        return AssistantMessage(content=self.credential)

    async def stream(
        self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None
    ) -> AsyncIterator:
        if False:
            yield None

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_live_provider_reloads_only_when_credential_changes() -> None:
    current = "first"
    built: list[_Provider] = []

    async def load() -> str:
        return current

    def build(credential: str) -> _Provider:
        provider = _Provider(credential)
        built.append(provider)
        return provider

    provider = LiveCredentialProvider(
        current,
        load=load,
        build=build,
        unavailable_message="missing",
    )
    assert (await provider.chat([], "model")).content == "first"
    assert len(built) == 1

    current = "second"
    assert (await provider.chat([], "model")).content == "second"
    assert len(built) == 2
    assert built[0].closed is True


@pytest.mark.asyncio
async def test_live_provider_does_not_fall_back_when_source_disappears() -> None:
    async def load():
        return None

    provider = LiveCredentialProvider(
        "stale",
        load=load,
        build=_Provider,
        unavailable_message="reconnect CLI",
    )
    with pytest.raises(RuntimeError, match="reconnect CLI"):
        await provider.chat([], "model")
