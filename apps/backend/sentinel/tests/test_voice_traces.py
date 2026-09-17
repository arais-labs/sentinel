from dataclasses import dataclass
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import VoiceTrace, VoiceTraceEvent
from app.services.voice.traces import TraceRecorder, trace_data


def test_voice_trace_redacts_credentials_media_and_nested_json_not_text_context():
    @dataclass
    class Payload:
        content: list

    output = trace_data(
        Payload(
            content=[
                {"type": "text", "text": "The user's full prompt"},
                {"type": "image", "data": "encoded image"},
                {"api_key": "private", "usage": {"input_tokens": 42}},
                {"content": '{"password":"private","summary":"result"}'},
            ]
        )
    )
    assert output["content"][0]["text"] == "The user's full prompt"
    assert output["content"][1]["data"] == "[redacted]"
    assert output["content"][2]["usage"]["input_tokens"] == 42
    assert "private" not in str(output)


@pytest.mark.asyncio
async def test_trace_events_persist_before_completion_and_errors_keep_prior_events():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            for model in (VoiceTrace, VoiceTraceEvent):
                await connection.run_sync(model.__table__.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        trace = TraceRecorder(factory, "turn", {"text": "Resume Review"})
        with pytest.raises(ValueError, match="provider offline"):
            async with trace:
                await trace.record("tool_result", {"chat_id": uuid4(), "status": "queued"})
                async with factory() as db:
                    assert (await db.get(VoiceTrace, trace.id)).status == "running"
                    assert len((await db.execute(select(VoiceTraceEvent))).scalars().all()) == 1
                raise ValueError("provider offline")
        async with factory() as db:
            row = await db.get(VoiceTrace, trace.id)
            assert row.status == "error"
            assert row.error == "provider offline"
            assert row.duration_ms is not None
            events = (
                (await db.execute(select(VoiceTraceEvent).order_by(VoiceTraceEvent.id)))
                .scalars()
                .all()
            )
            assert [event.kind for event in events] == ["tool_result", "exception"]
    finally:
        await engine.dispose()
