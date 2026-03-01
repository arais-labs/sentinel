"""Redacts sensitive credential/token patterns from logs and tool outputs."""

from __future__ import annotations

import re

_SIMPLE_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{36}\b"),
    re.compile(r"\bgho_[A-Za-z0-9]{36}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{82}\b"),
    re.compile(r"\bxoxb-[A-Za-z0-9-]+\b"),
    re.compile(r"\bxoxp-[A-Za-z0-9-]+\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b"),
    re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"),
]
_BEARER_PATTERN = re.compile(r"\bBearer\s+([A-Za-z0-9._\-+/=]{16,})")
_POSTGRES_PATTERN = re.compile(r"\b(postgresql(?:\+asyncpg)?://[^:\s/@]+:)([^@\s]+)(@)")
_PEM_PATTERN = re.compile(
    r"(-----BEGIN [A-Z0-9 ]+-----)([\s\S]*?)(-----END [A-Z0-9 ]+-----)",
    re.MULTILINE,
)


def scrub(text: str) -> str:
    if not text:
        return text

    value = text
    for pattern in _SIMPLE_SECRET_PATTERNS:
        value = pattern.sub(lambda match: _redact(match.group(0)), value)

    value = _BEARER_PATTERN.sub(lambda match: f"Bearer {_redact(match.group(1))}", value)
    value = _POSTGRES_PATTERN.sub(
        lambda match: f"{match.group(1)}{_redact(match.group(2))}{match.group(3)}",
        value,
    )
    value = _PEM_PATTERN.sub(lambda match: _redact_pem(match.group(1), match.group(3)), value)
    return value


def _redact(secret: str) -> str:
    if len(secret) <= 10:
        start = secret[:3]
        end = secret[-2:] if len(secret) > 3 else ""
        return f"{start}...{end}"
    return f"{secret[:6]}...{secret[-4:]}"


def _redact_pem(header: str, footer: str) -> str:
    return f"{header}\n[REDACTED]\n{footer}"
