from __future__ import annotations

from app.services.agent.tool_image_reinjection import (
    ToolImageReinjectionPolicy,
    build_tool_image_reinjection_messages,
)
from app.services.llm.types import ImageContent, ToolResultMessage


PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9s8bVgAAAABJRU5ErkJggg=="


def test_tool_image_reinjection_respects_limits_and_dedupes():
    result = build_tool_image_reinjection_messages(
        [
            ToolResultMessage(
                tool_call_id="call_1",
                tool_name="browser_screenshot",
                content='{"image_base64":"[omitted]"}',
                metadata={
                    "attachments": [
                        {
                            "base64": PNG_B64,
                            "mime_type": "image/png",
                            "size_bytes": 1024,
                            "sha256": "samehash",
                        },
                        {
                            "base64": PNG_B64,
                            "mime_type": "image/png",
                            "size_bytes": 1024,
                            "sha256": "samehash",
                        },
                    ]
                },
            ),
            ToolResultMessage(
                tool_call_id="call_2",
                tool_name="browser_screenshot",
                content='{"image_base64":"[omitted]"}',
                metadata={
                    "attachments": [
                        {
                            "base64": PNG_B64,
                            "mime_type": "image/png",
                            "size_bytes": 5_000_000,
                            "sha256": "toolarge",
                        }
                    ]
                },
            ),
        ],
        policy=ToolImageReinjectionPolicy(
            enabled=True,
            max_images_per_turn=2,
            max_bytes_per_image=2_000_000,
            max_total_bytes_per_turn=4_000_000,
        ),
        seen_hashes=set(),
    )

    assert result.selected_count == 1
    assert result.skipped_count == 2
    assert len(result.messages) == 1
    msg = result.messages[0]
    image_blocks = [block for block in msg.content if isinstance(block, ImageContent)]
    assert len(image_blocks) == 1
    assert image_blocks[0].data == PNG_B64
