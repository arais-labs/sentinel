from sentral import (
    AgentEvent,
    ConversationItem,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
)
from app.services.agent_runtime_adapters.presentation import RunPresentation
from sentral.llm.runtime_conversions import (
    runtime_event_to_sentinel_event,
    runtime_item_to_sentinel_message,
)
from app.services.ws.ws_manager import ConnectionManager


def test_stream_and_saved_items_share_identity_without_changing_history_order():
    timeline = RunPresentation()
    text = AgentEvent(type="text_delta", delta="Hello")
    timeline.event(text)
    stamp = text.metadata["presentation"]
    more = AgentEvent(type="text_delta", delta=" world")
    timeline.event(more)
    assert more.metadata["presentation"] == stamp
    wire = ConnectionManager()._event_payload(runtime_event_to_sentinel_event(text))
    assert wire["presentation"] == stamp

    call = ToolCallBlock(id="tool-1", name="runtime", arguments={})
    event = AgentEvent(type="toolcall_start", tool_call=call)
    timeline.event(event)
    tool_stamp = event.metadata["presentation"]
    assistant = ConversationItem(
        id="assistant", role="assistant", content=[TextBlock(text="Hello world"), call]
    )
    original_timestamp = assistant.timestamp
    timeline.item(assistant)
    assert assistant.timestamp == original_timestamp
    assert runtime_item_to_sentinel_message(assistant).presentation == stamp

    result = ToolResultBlock(tool_call_id="tool-1", tool_name="runtime", content="ok")
    tool_item = ConversationItem(id="result", role="tool", content=[result])
    timeline.item(tool_item)
    assert runtime_item_to_sentinel_message(tool_item).metadata["presentation"] == tool_stamp

    next_text = AgentEvent(type="text_delta", delta="Next iteration")
    timeline.event(next_text)
    assert next_text.metadata["presentation"]["id"] != stamp["id"]


def test_nonstream_and_user_messages_keep_existing_presentation():
    timeline = RunPresentation()
    for role in ("user", "assistant"):
        item = ConversationItem(id=role, role=role, content=[TextBlock(text="hello")])
        timeline.item(item)
        assert "presentation" not in item.metadata
