from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime
from uuid import uuid4
import time

from fastapi import WebSocket

from sentral.llm.generic.types import AgentEvent, ToolCallContent


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()
        self._layout_clients: dict[str, set[WebSocket]] = defaultdict(set)
        self._layout_pending: dict[str, tuple[WebSocket, asyncio.Future]] = {}

    async def connect(self, session_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections[session_id].add(websocket)

    async def disconnect(self, session_id: str, websocket: WebSocket) -> None:
        self._layout_clients[session_id].discard(websocket)
        for socket, future in list(self._layout_pending.values()):
            if socket is websocket and not future.done():
                future.set_exception(
                    RuntimeError("Layout client disconnected; inspect before retrying")
                )
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

    def layout_message(self, session_id: str, websocket: WebSocket, payload: dict) -> bool:
        if payload.get("type") == "layout_ready":
            clients = self._layout_clients[session_id]
            if payload.get("ready") is True:
                clients.add(websocket)
            else:
                clients.discard(websocket)
            return True
        if payload.get("type") == "layout_result":
            entry = self._layout_pending.get(str(payload.get("request_id", "")))
            if entry and entry[0] is websocket and not entry[1].done():
                entry[1].set_result(payload.get("result"))
            return True
        return False

    async def request_layout(self, session_id: str, command: dict, timeout: float = 10) -> dict:
        clients = self._layout_clients.get(session_id, set())
        if not clients:
            raise RuntimeError(
                "No visible layout is connected for this session; open it in Sentinel and wait for reconnection"
            )
        if len(clients) > 1:
            raise RuntimeError(
                "This session is displayed in multiple Sentinel windows; leave one visible layout connected"
            )
        socket = next(iter(clients))
        if any(client is socket for client, _ in self._layout_pending.values()):
            raise RuntimeError("A layout request is already in progress")
        request_id = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        self._layout_pending[request_id] = (socket, future)
        try:
            await socket.send_json(
                {
                    "type": "layout_request",
                    "request_id": request_id,
                    "expires_at": time.time() * 1000 + timeout * 1000,
                    "command": command,
                }
            )
            result = await asyncio.wait_for(future, timeout)
            if not isinstance(result, dict):
                raise RuntimeError("Invalid layout response")
            return result
        except asyncio.TimeoutError as exc:
            raise RuntimeError("Layout response timed out; inspect before retrying") from exc
        finally:
            self._layout_pending.pop(request_id, None)

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

    async def broadcast_thinking_start(self, session_id: str) -> None:
        await self.broadcast(
            session_id,
            {
                "type": "thinking_start",
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

    async def broadcast_runtime_ready(self, session_id: str) -> None:
        await self.broadcast(
            session_id,
            {
                "type": "runtime_ready",
                "session_id": session_id,
            },
        )

    async def broadcast_sub_agent_started(
        self, session_id: str, task_id: str, objective: str
    ) -> None:
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
        if event.conversation_message is not None:
            payload["message"] = event.conversation_message
        if event.presentation is not None:
            payload["presentation"] = event.presentation
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
            tool_result_payload: dict[str, object] = {
                "tool_call_id": event.tool_result.tool_call_id,
                "tool_name": event.tool_result.tool_name,
                "content": event.tool_result.content,
                "is_error": event.tool_result.is_error,
                "metadata": event.tool_result.metadata,
            }
            if event.tool_result.tool_arguments is not None:
                tool_result_payload["tool_arguments"] = event.tool_result.tool_arguments
            payload["tool_result"] = tool_result_payload
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
