import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from telegram.error import RetryAfter, TimedOut

from app.services.telegram.delivery import RichReplyDraft, send_rich_message


@pytest.mark.asyncio
async def test_rate_limit_retries_only_rejected_send():
    bot = SimpleNamespace(do_api_request=AsyncMock(side_effect=[RetryAfter(2), {"message_id": 1}]))
    with patch("app.services.telegram.delivery.asyncio.sleep", new=AsyncMock()) as sleep:
        result = await send_rich_message(bot, 123, "**Hello**")
    assert result == {"message_id": 1}
    sleep.assert_awaited_once_with(2)
    assert bot.do_api_request.await_count == 2


@pytest.mark.asyncio
async def test_ambiguous_send_timeout_is_not_duplicated():
    bot = SimpleNamespace(do_api_request=AsyncMock(side_effect=TimedOut()))
    with pytest.raises(TimedOut):
        await send_rich_message(bot, 123, "Hello")
    bot.do_api_request.assert_awaited_once()


@pytest.mark.asyncio
async def test_rate_limit_exhaustion_propagates():
    bot = SimpleNamespace(do_api_request=AsyncMock(side_effect=RetryAfter(1)))
    with patch("app.services.telegram.delivery.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(RetryAfter):
            await send_rich_message(bot, 123, "Hello")
    assert bot.do_api_request.await_count == 3


@pytest.mark.asyncio
async def test_draft_coalesces_updates_and_stops_cleanly():
    received = asyncio.Event()

    async def request(*args, **kwargs):
        received.set()
        return True

    bot = SimpleNamespace(do_api_request=AsyncMock(side_effect=request))
    draft = RichReplyDraft(bot, 123, thread_id=7)
    draft.update("One")
    draft.update("One two")
    draft.update("One two three")
    await asyncio.wait_for(received.wait(), 1)
    await draft.close()
    bot.do_api_request.assert_awaited_once()
    payload = bot.do_api_request.call_args.kwargs["api_kwargs"]
    assert payload["rich_message"] == {"markdown": "One two three"}
    assert payload["message_thread_id"] == 7
    assert payload["draft_id"] != 0
    assert draft._task is None
