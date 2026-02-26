from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime

from fastapi import WebSocket

from app.services.llm.types import AgentEvent, ToolCallContent


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, session_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections[session_id].add(websocket)

    async def disconnect(self, session_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            sockets = self._connections.get(session_id)
            if not sockets:
                return
            sockets.discard(websocket)
            if not sockets:
                self._connections.pop(session_id, None)

    async def broadcast(self, session_id: str, data: dict) -> None:
        sockets = list(self._connections.get(session_id, set()))
        for socket in sockets:
            try:
                await socket.send_json(data)
            except Exception:
                await self.disconnect(session_id, socket)

    def get_active_count(self, session_id: str) -> int:
        return len(self._connections.get(session_id, set()))

    async def broadcast_message_ack(
        self,
        session_id: str,
        message_id: str,
        content: str,
        created_at: datetime | None,
        metadata: dict | None = None,
    ) -> None:
        await self.broadcast(
            session_id,
            {
                "type": "message_ack",
                "session_id": session_id,
                "message_id": message_id,
                "content": content,
                "created_at": self._iso(created_at),
                "metadata": metadata or {},
            },
        )

    async def broadcast_agent_thinking(self, session_id: str) -> None:
        await self.broadcast(
            session_id,
            {
                "type": "agent_thinking",
                "session_id": session_id,
            },
        )

    async def broadcast_agent_event(self, session_id: str, event: AgentEvent) -> None:
        payload = self._event_payload(event)
        payload["session_id"] = session_id
        await self.broadcast(session_id, payload)

    async def broadcast_agent_error(self, session_id: str, message: str) -> None:
        await self.broadcast(
            session_id,
            {
                "type": "agent_error",
                "session_id": session_id,
                "message": message,
            },
        )

    async def broadcast_done(self, session_id: str, stop_reason: str) -> None:
        await self.broadcast(
            session_id,
            {
                "type": "done",
                "session_id": session_id,
                "stop_reason": stop_reason,
            },
        )

    async def broadcast_sub_agent_started(self, session_id: str, task_id: str, objective: str) -> None:
        await self.broadcast(
            session_id,
            {
                "type": "sub_agent_started",
                "session_id": session_id,
                "task_id": task_id,
                "objective": objective,
            },
        )

    async def broadcast_sub_agent_completed(
        self,
        session_id: str,
        task_id: str,
        status: str,
        result: dict | None,
    ) -> None:
        await self.broadcast(
            session_id,
            {
                "type": "sub_agent_completed",
                "session_id": session_id,
                "task_id": task_id,
                "status": status,
                "result": result,
            },
        )

    def _event_payload(self, event: AgentEvent) -> dict:
        payload: dict = {"type": event.type}
        if event.delta is not None:
            payload["delta"] = event.delta
        if event.content_index is not None:
            payload["content_index"] = event.content_index
        if event.stop_reason is not None:
            payload["stop_reason"] = event.stop_reason
        if event.error is not None:
            payload["error"] = event.error
        if event.tool_call is not None:
            payload["tool_call"] = self._tool_call_payload(event.tool_call)
        if event.tool_result is not None:
            payload["tool_result"] = {
                "tool_call_id": event.tool_result.tool_call_id,
                "tool_name": event.tool_result.tool_name,
                "content": event.tool_result.content,
                "is_error": event.tool_result.is_error,
                "metadata": event.tool_result.metadata,
            }
        if event.iteration is not None:
            payload["iteration"] = event.iteration
        if event.max_iterations is not None:
            payload["max_iterations"] = event.max_iterations
        return payload

    def _tool_call_payload(self, call: ToolCallContent) -> dict:
        return {
            "id": call.id,
            "name": call.name,
            "arguments": call.arguments,
        }

    def _iso(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.isoformat()
