from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock
import wave

import httpx
import pytest

from app.services.voice import VoiceGateway, VoiceUnavailable
from app.services.voice.runtime import VoiceRuntime, INSTALL_VERSION


def wav(rate=16000):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"\0\0" * rate)
    return output.getvalue()


@pytest.mark.asyncio
async def test_audio_is_validated_before_worker_and_never_sent_to_http():
    runtime = SimpleNamespace(transcribe=AsyncMock(return_value=" hello "))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("No audio HTTP calls"))
    ):
        voice = VoiceGateway(runtime=runtime)
        assert await voice.transcribe(wav(), "lease") == "hello"
        runtime.transcribe.assert_awaited_once_with(wav(), "lease")
        for invalid in (b"bad", wav(48000), wav()[:-100]):
            with pytest.raises(ValueError):
                await voice.transcribe(invalid, "lease")
        assert runtime.transcribe.await_count == 1


@pytest.mark.asyncio
async def test_runtime_leases_cleanup_and_isolation(tmp_path, monkeypatch):
    runtime = VoiceRuntime(tmp_path)
    runtime.python.parent.mkdir(parents=True)
    runtime.python.touch()
    (runtime.root / "model").mkdir()
    (runtime.root / "model/model.bin").touch()
    (runtime.root / "installed.json").write_text(INSTALL_VERSION)
    assert not runtime.installed
    (runtime.root / "speech").mkdir()
    (runtime.root / "speech/kokoro-v1.0.int8.onnx").touch()
    (runtime.root / "speech/voices-v1.0.bin").touch()
    assert runtime.installed
    outside = tmp_path / "unrelated.txt"
    outside.write_text("keep")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-inherit")
    monkeypatch.setenv("PYTHONPATH", "must-not-inherit")
    env = runtime.environment(offline=True)
    assert "ANTHROPIC_API_KEY" not in env and "PYTHONPATH" not in env
    assert env["HF_HUB_OFFLINE"] == "1"
    monkeypatch.setattr(runtime, "_start", AsyncMock())
    terminate = AsyncMock()
    monkeypatch.setattr(runtime, "_terminate", terminate)
    first, second = await runtime.acquire(), await runtime.acquire()
    await runtime.release(first)
    terminate.assert_not_awaited()
    await runtime.release(second)
    assert terminate.await_count == 1
    await runtime.remove()
    assert not runtime.root.exists()
    assert outside.read_text() == "keep"


@pytest.mark.asyncio
async def test_synthesis_is_bounded_and_uses_managed_worker(tmp_path, monkeypatch):
    runtime = VoiceRuntime(tmp_path)
    request = AsyncMock(return_value={"audio": "encoded-wav"})
    monkeypatch.setattr(runtime, "_request", request)
    assert await runtime.synthesize("Hello.", "lease") == {
        "audio": "encoded-wav",
        "mime_type": "audio/wav",
    }
    request.assert_awaited_once_with({"text": "Hello.", "speed": 1.05}, "lease")
    for invalid in ("", "  ", "x" * 401):
        with pytest.raises(ValueError):
            await runtime.synthesize(invalid, "lease")
    assert request.await_count == 1
    for invalid_speed in (0.5, 2.1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            await runtime.synthesize("Hello.", "lease", speed=invalid_speed)
    assert request.await_count == 1
    await runtime.synthesize("Faster.", "lease", speed=1.5)
    request.assert_awaited_with({"text": "Faster.", "speed": 1.5}, "lease")


@pytest.mark.asyncio
async def test_synthesis_requires_live_lease(tmp_path):
    with pytest.raises(VoiceUnavailable, match="expired"):
        await VoiceRuntime(tmp_path).synthesize("Hello.", "unknown")
