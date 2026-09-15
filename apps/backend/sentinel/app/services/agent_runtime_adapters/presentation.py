"""Stable presentation identities shared by streamed events and saved messages.

Only display metadata is added; model history and execution order are unchanged.
"""

from datetime import UTC, datetime
from uuid import uuid4

from sentral import AgentEvent, ConversationItem, ToolResultBlock


class RunPresentation:
    def __init__(self):
        self.text: dict | None = None
        self.tools: dict[str, dict] = {}

    @staticmethod
    def _entry() -> dict:
        return {"id": str(uuid4()), "created_at": datetime.now(UTC).isoformat()}

    def event(self, event: AgentEvent) -> None:
        entry = None
        if event.type == "text_delta" and event.delta:
            if self.text is None:
                self.text = self._entry()
            entry = self.text
        elif event.type == "toolcall_start" and event.tool_call:
            entry = self.tools.setdefault(event.tool_call.id, self._entry())
        elif event.type == "tool_result" and event.tool_result:
            entry = self.tools.setdefault(event.tool_result.tool_call_id, self._entry())
        if entry:
            event.metadata["presentation"] = entry

    def item(self, item: ConversationItem) -> None:
        entry = None
        if item.role == "assistant":
            entry, self.text = self.text, None
        elif item.role == "tool":
            block = next((b for b in item.content if isinstance(b, ToolResultBlock)), None)
            if block:
                entry = self.tools.get(block.tool_call_id)
        if entry:
            item.metadata["presentation"] = entry
