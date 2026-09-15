"""Native rich-message delivery shared by bridge replies and module sends."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any
from uuid import uuid4

from telegram.error import NetworkError, RetryAfter

logger = logging.getLogger(__name__)


def retry_seconds(error: RetryAfter) -> float:
    value = error.retry_after
    return value.total_seconds() if isinstance(value, timedelta) else float(value)


async def send_rich_message(
    bot: Any,
    chat_id: int,
    text: str,
    *,
    reply_to: int | None = None,
    thread_id: int | None = None,
) -> Any:
    payload: dict[str, Any] = {"chat_id": chat_id, "rich_message": {"markdown": text}}
    if reply_to is not None:
        payload["reply_parameters"] = {"message_id": reply_to}
    if thread_id is not None:
        payload["message_thread_id"] = thread_id
    for attempt in range(3):
        try:
            return await bot.do_api_request("sendRichMessage", api_kwargs=payload)
        except RetryAfter as exc:
            if attempt == 2:
                raise
            await asyncio.sleep(retry_seconds(exc))
    # Network timeouts are deliberately not retried: Telegram may have accepted
    # the message, and sendRichMessage has no idempotency key.


class RichReplyDraft:
    """Coalesce previews off the agent event path; persist the final answer once."""

    def __init__(self, bot: Any, chat_id: int, *, thread_id: int | None = None):
        self.bot = bot
        self.chat_id = chat_id
        self.thread_id = thread_id
        self.draft_id = uuid4().int & 0x7FFFFFFF or 1
        self.text = ""
        self._changed = asyncio.Event()
        self._task: asyncio.Task | None = None

    def update(self, text: str) -> None:
        if text == self.text:
            return
        self.text = text
        self._changed.set()
        if self._task is None:
            self._task = asyncio.create_task(self._pump())

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _pump(self) -> None:
        while True:
            try:
                # Refresh while tools run: Telegram draft previews expire after 30s.
                await asyncio.wait_for(self._changed.wait(), timeout=20)
            except TimeoutError:
                pass
            self._changed.clear()
            payload: dict[str, Any] = {
                "chat_id": self.chat_id,
                "draft_id": self.draft_id,
                "rich_message": {"markdown": self.text},
            }
            if self.thread_id is not None:
                payload["message_thread_id"] = self.thread_id
            try:
                await self.bot.do_api_request("sendRichMessageDraft", api_kwargs=payload)
            except RetryAfter as exc:
                await asyncio.sleep(retry_seconds(exc))
                self._changed.set()
            except NetworkError:
                # Replacing the same ephemeral draft is safe to retry.
                await asyncio.sleep(2)
                self._changed.set()
            except Exception:
                logger.exception("Telegram draft unavailable; final reply will still be sent")
                return
            await asyncio.sleep(1)
