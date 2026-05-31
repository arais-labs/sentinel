"""In-memory conversation store for the standalone runtime contracts."""

from __future__ import annotations

from collections import defaultdict

from app.sentral.types import ConversationItem


class InMemoryConversationStore:
    """Simple process-local conversation store for tests and embeddings."""

    def __init__(self) -> None:
        self._history: dict[str, list[ConversationItem]] = defaultdict(list)

    async def load_history(self, conversation_id: str) -> list[ConversationItem]:
        return list(self._history.get(conversation_id, []))

    async def append_items(
        self,
        conversation_id: str,
        items: list[ConversationItem],
    ) -> None:
        self._history.setdefault(conversation_id, [])
        self._history[conversation_id].extend(items)

    async def replace_history(
        self,
        conversation_id: str,
        items: list[ConversationItem],
    ) -> None:
        self._history[conversation_id] = list(items)
