"""Local speech for the Voice agent: transcription in, status out. Turns run as a session."""

from __future__ import annotations

import io
import wave

MAX_AUDIO_BYTES = 960_044


class VoiceUnavailable(ValueError):
    pass


class VoiceGateway:
    def __init__(self, provider=None, runtime=None):
        self.provider, self.runtime = provider, runtime

    def status(self):
        runtime = self.runtime.status() if self.runtime else {"installed": False}
        issues = []
        if not runtime["installed"]:
            issues.append("Install the managed Whisper + Kokoro speech engine.")
        if self.provider is None:
            issues.append("Configure an LLM provider in Settings → LLM Providers.")
        hint = self.provider.resolve_generation_hint("fast") if self.provider else None
        return {
            "ready": not issues,
            "runtime": runtime,
            "provider_configured": self.provider is not None,
            "provider": hint[0] if hint else None,
            "model": hint[1] if hint else None,
            "issues": issues,
        }

    async def transcribe(self, audio: bytes, lease_id: str) -> str:
        try:
            with wave.open(io.BytesIO(audio)) as wav:
                if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000):
                    raise ValueError("Expected mono 16 kHz PCM16 WAV audio.")
                if not 3200 <= wav.getnframes() <= 480000:
                    raise ValueError("Audio must be between 0.2 and 30 seconds.")
                if len(wav.readframes(wav.getnframes())) != wav.getnframes() * 2:
                    raise ValueError("Audio is incomplete.")
        except (wave.Error, EOFError) as exc:
            raise ValueError("Invalid WAV audio.") from exc
        if self.runtime is None:
            raise VoiceUnavailable("Speech runtime is unavailable.")
        text = (await self.runtime.transcribe(audio, lease_id)).strip()
        return "" if text.startswith("[") and text.endswith("]") else text[:10_000]
