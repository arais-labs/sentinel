from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from app.services.llm.types import ImageContent, TextContent, ToolResultMessage, UserMessage


@dataclass(slots=True)
class ToolImageReinjectionPolicy:
    enabled: bool = True
    max_images_per_turn: int = 2
    max_bytes_per_image: int = 2_000_000
    max_total_bytes_per_turn: int = 4_000_000


@dataclass(slots=True)
class ToolImageReinjectionResult:
    messages: list[UserMessage]
    selected_count: int
    skipped_count: int


def build_tool_image_reinjection_messages(
    tool_results: list[ToolResultMessage],
    *,
    policy: ToolImageReinjectionPolicy,
    seen_hashes: set[str] | None = None,
) -> ToolImageReinjectionResult:
    if not policy.enabled or policy.max_images_per_turn <= 0:
        return ToolImageReinjectionResult(messages=[], selected_count=0, skipped_count=0)

    selected: list[dict[str, Any]] = []
    skipped = 0
    total_bytes = 0
    max_images = max(1, int(policy.max_images_per_turn))
    max_per_image = max(1, int(policy.max_bytes_per_image))
    max_total = max(1, int(policy.max_total_bytes_per_turn))
    seen = seen_hashes if seen_hashes is not None else set()

    for result in tool_results:
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        attachments = metadata.get("attachments")
        if not isinstance(attachments, list):
            continue

        for att in attachments:
            if not isinstance(att, dict):
                continue
            payload = att.get("base64")
            if not isinstance(payload, str) or not payload:
                continue

            size_bytes_raw = att.get("size_bytes")
            size_bytes = int(size_bytes_raw) if isinstance(size_bytes_raw, int) else _estimate_base64_size(payload)
            mime_type_raw = att.get("mime_type")
            mime_type = mime_type_raw if isinstance(mime_type_raw, str) and mime_type_raw else "image/png"
            hash_raw = att.get("sha256")
            image_hash = hash_raw if isinstance(hash_raw, str) and hash_raw else _hash_base64(payload)

            if image_hash in seen:
                skipped += 1
                continue
            if size_bytes > max_per_image:
                skipped += 1
                continue
            if len(selected) >= max_images:
                skipped += 1
                continue
            if total_bytes + size_bytes > max_total:
                skipped += 1
                continue

            selected.append(
                {
                    "base64": payload,
                    "mime_type": mime_type,
                    "size_bytes": size_bytes,
                    "sha256": image_hash,
                    "tool_name": result.tool_name,
                    "tool_call_id": result.tool_call_id,
                }
            )
            seen.add(image_hash)
            total_bytes += size_bytes

    if not selected:
        return ToolImageReinjectionResult(messages=[], selected_count=0, skipped_count=skipped)

    content: list[TextContent | ImageContent] = [
        TextContent(
            text=(
                "Tool image evidence from the latest tool results. "
                "Use these screenshots to reason about current UI/page state."
            )
        )
    ]
    for idx, item in enumerate(selected, start=1):
        source_parts: list[str] = []
        tool_name = str(item.get("tool_name") or "").strip()
        call_id = str(item.get("tool_call_id") or "").strip()
        if tool_name:
            source_parts.append(tool_name)
        if call_id:
            source_parts.append(f"call={call_id}")
        source = " ".join(source_parts) if source_parts else "unknown"
        content.append(TextContent(text=f"Image {idx}: source={source}"))
        content.append(
            ImageContent(
                media_type=str(item["mime_type"]),
                data=str(item["base64"]),
            )
        )

    if skipped > 0:
        content.append(TextContent(text=f"Note: {skipped} additional image(s) were skipped due to reinjection limits."))

    return ToolImageReinjectionResult(
        messages=[
            UserMessage(
                content=content,
                metadata={
                    "source": "tool_image_reinjection",
                    "selected_count": len(selected),
                    "skipped_count": skipped,
                },
            )
        ],
        selected_count=len(selected),
        skipped_count=skipped,
    )


def _hash_base64(payload: str) -> str:
    return hashlib.sha256(payload.encode("ascii", errors="ignore")).hexdigest()


def _estimate_base64_size(payload: str) -> int:
    compact = "".join(payload.split())
    if not compact:
        return 0
    padding = compact[-2:].count("=")
    return max(0, (len(compact) * 3) // 4 - padding)
