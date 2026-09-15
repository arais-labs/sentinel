import asyncio
import pytest
import httpx
from sentral.llm import claude_credentials as credentials
from sentral.llm.providers.anthropic import AnthropicProvider
from sentral.llm.generic.types import UserMessage


@pytest.mark.asyncio
async def test_concurrent_renewals_run_one_probe(monkeypatch):
    state = {"token": "old", "probes": 0}

    async def read():
        return state["token"]

    async def probe():
        state["probes"] += 1
        await asyncio.sleep(0.01)
        state["token"] = "new"

    monkeypatch.setattr(credentials, "read_claude_access_token", read)
    monkeypatch.setattr(credentials, "run_claude_refresh_probe", probe)
    assert (
        await asyncio.gather(*(credentials.renew_claude_access_token("old") for _ in range(5)))
        == ["new"] * 5
    )
    assert state["probes"] == 1


@pytest.mark.asyncio
async def test_unchanged_token_does_not_report_success(monkeypatch):
    async def read():
        return "old"

    async def probe():
        pass

    monkeypatch.setattr(credentials, "read_claude_access_token", read)
    monkeypatch.setattr(credentials, "run_claude_refresh_probe", probe)
    with pytest.raises(RuntimeError, match="could not be renewed"):
        await credentials.renew_claude_access_token("old")


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("still_unauthorized", [False, True])
async def test_provider_retries_auth_only_once(monkeypatch, streaming, still_unauthorized):
    calls, renewals = [], []

    async def renew(previous):
        renewals.append(previous)
        return "sk-ant-oat-new"

    monkeypatch.setattr(credentials, "renew_claude_access_token", renew)

    def handler(request):
        calls.append(request.headers["authorization"])
        if len(calls) == 1 or still_unauthorized:
            return httpx.Response(401, json={"error": "expired"})
        data = {
            "content": [{"type": "text", "text": "OK"}],
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "stop_reason": "end_turn",
        }
        if streaming:
            import json

            frames = [{"type": "message_start", "message": data}, {"type": "message_stop"}]
            return httpx.Response(200, text="\n".join("data: " + json.dumps(f) for f in frames))
        return httpx.Response(200, json=data)

    provider = AnthropicProvider(
        "sk-ant-oat-old",
        renew_credentials=renew,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    async def run():
        if streaming:
            return [event async for event in provider.stream([UserMessage(content="Hello")])]
        return await provider.chat([UserMessage(content="Hello")])

    if still_unauthorized:
        with pytest.raises(httpx.HTTPStatusError):
            await run()
    else:
        await run()
    assert calls == ["Bearer sk-ant-oat-old", "Bearer sk-ant-oat-new"]
    assert renewals == ["sk-ant-oat-old"]


@pytest.mark.asyncio
async def test_keychain_takes_precedence_over_stale_file(monkeypatch, tmp_path):
    import json

    directory = tmp_path / ".claude"
    directory.mkdir()
    (directory / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "stale"}})
    )
    monkeypatch.setattr(credentials.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(credentials.sys, "platform", "darwin")

    class Process:
        returncode = 0

        async def communicate(self):
            return json.dumps({"claudeAiOauth": {"accessToken": "fresh"}}).encode(), b""

    async def spawn(*args, **kwargs):
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    assert await credentials.read_claude_access_token() == "fresh"


@pytest.mark.asyncio
async def test_probe_stops_after_one_second_without_capturing_output(monkeypatch):
    calls = []

    class Process:
        returncode = None
        pid = 12345
        waits = 0

        async def wait(self):
            self.waits += 1
            if self.waits == 1:
                await asyncio.Event().wait()
            self.returncode = 0
            return 0

    process = Process()

    async def spawn(*args, **kwargs):
        assert args == ("/test/claude", "-p", "Sentinel Prob")
        assert kwargs["stdout"] == asyncio.subprocess.DEVNULL
        assert kwargs["stderr"] == asyncio.subprocess.DEVNULL
        return process

    monkeypatch.setattr(credentials.shutil, "which", lambda _: "/test/claude")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(credentials.os, "killpg", lambda pid, sig: calls.append((pid, sig)))
    await asyncio.wait_for(credentials.run_claude_refresh_probe(), 2)
    assert calls == [(12345, credentials.signal.SIGTERM)]
    assert process.returncode == 0
