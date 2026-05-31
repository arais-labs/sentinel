"""Lift base64 image blobs out of tool results into structured attachments."""

from __future__ import annotations

import base64
import binascii
import hashlib
from typing import Any

# Cap per-attachment base64 length so a malformed tool can't dump unbounded
# bytes into message metadata. 2M chars ≈ 1.5MB binary image.
MAX_INLINE_IMAGE_BASE64_CHARS = 2_000_000


def _detect_image_mime(data: bytes) -> str | None:
    if len(data) < 12:
        return None
    is_png = data[:4] == b"\x89PNG"
    is_jpeg = data[:3] == b"\xff\xd8\xff"
    is_gif = data[:4] in (b"GIF8",)
    is_webp = data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if is_png:
        return "image/png"
    if is_jpeg:
        return "image/jpeg"
    if is_gif:
        return "image/gif"
    if is_webp:
        return "image/webp"
    return None


def _extract_image_payload(value: str, *, key_hint: str) -> tuple[str, str, int] | None:
    # Require both a hint-y key name AND valid magic bytes — that combo
    # avoids misclassifying long opaque strings (tokens, hashes) as images.
    hint = key_hint.lower()
    if "image" not in hint and "screenshot" not in hint and "base64" not in hint:
        return None

    payload = value.strip()
    declared_mime: str | None = None
    if payload.startswith("data:image/"):
        comma_idx = payload.find(",")
        if comma_idx == -1:
            return None
        header = payload[:comma_idx]
        declared_mime = header[5:].split(";")[0].strip().lower()
        payload = payload[comma_idx + 1 :]

    payload = "".join(payload.split())
    if len(payload) < 64:
        return None
    if len(payload) % 4 != 0:
        return None

    try:
        decoded = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        return None

    detected_mime = _detect_image_mime(decoded)
    if detected_mime is None:
        return None
    mime_type = (
        declared_mime if declared_mime and declared_mime.startswith("image/") else detected_mime
    )
    if mime_type == "image/jpg":
        mime_type = "image/jpeg"
    return (payload, mime_type, len(decoded))


def extract_attachments(
    value: Any,
    *,
    attachments: list[dict[str, Any]],
    path: str = "",
    key_hint: str = "",
) -> Any:
    """Walk `value` and lift base64 image blobs into `attachments`, leaving
    a "[base64 image omitted...]" placeholder in their place."""
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else key
            cleaned[key] = extract_attachments(
                item,
                attachments=attachments,
                path=child_path,
                key_hint=key,
            )
        return cleaned

    if isinstance(value, list):
        return [
            extract_attachments(
                item,
                attachments=attachments,
                path=f"{path}[{idx}]",
                key_hint=key_hint,
            )
            for idx, item in enumerate(value)
        ]

    if isinstance(value, str):
        parsed = _extract_image_payload(value, key_hint=key_hint)
        if parsed is None:
            return value
        attachment_value, mime_type, size_bytes = parsed
        if len(attachment_value) > MAX_INLINE_IMAGE_BASE64_CHARS:
            attachment_value = attachment_value[:MAX_INLINE_IMAGE_BASE64_CHARS]
        attachments.append(
            {
                "path": path or key_hint or "payload",
                "base64": attachment_value,
                "mime_type": mime_type,
                "size_bytes": size_bytes,
                "sha256": hashlib.sha256(
                    attachment_value.encode("ascii", errors="ignore")
                ).hexdigest(),
            }
        )
        return f"[base64 image omitted from context: {len(value)} chars]"

    return value


__all__ = ["MAX_INLINE_IMAGE_BASE64_CHARS", "extract_attachments"]
