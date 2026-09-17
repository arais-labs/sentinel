from app.models import Message
from app.services.sessions.context_usage import reported_input_tokens, latest_request_input_tokens


def test_only_provider_usage_is_accepted():
    for value in (
        None,
        {},
        {"estimated_context_tokens": 100},
        {"usage": {"input_tokens": -1}},
        {"usage": {"input_tokens": True}},
    ):
        assert reported_input_tokens(value) is None
    assert reported_input_tokens({"usage": {"input_tokens": 0}}) == 0
    assert reported_input_tokens({"usage": {"input_tokens": 123}}) == 123


def test_missing_latest_usage_does_not_fall_back_to_earlier_request():
    messages = [
        Message(
            role="assistant",
            content="",
            metadata_json={"provider_usage": {"usage": {"input_tokens": 123}}},
        )
    ]
    assert latest_request_input_tokens(messages) == 123
    messages.append(
        Message(role="assistant", content="x" * 10000, token_count=5000, metadata_json={})
    )
    assert latest_request_input_tokens(messages) is None


def conversation_messages(db):
    return [
        m for m in db.storage[Message] if (m.metadata_json or {}).get("purpose") != "compaction"
    ]


def test_auto_compaction_uses_reported_input_and_ignores_stale_usage():
    import asyncio
    from app.models import Session, SessionSummary
    from app.services.sessions.compaction import CompactionService
    from tests.compaction_fixtures import HandoffProvider
    from tests.fake_db import FakeDB

    db = FakeDB()
    session = Session(user_id="local", status="active")
    db.add(session)
    for i in range(14):
        db.add(
            Message(
                session_id=session.id,
                role="user" if i % 2 == 0 else "assistant",
                content="turn",
                metadata_json={},
            )
        )
    db.storage[Message][-1].metadata_json = {"provider_usage": {"usage": {"input_tokens": 5000}}}
    service = CompactionService(HandoffProvider())
    assert asyncio.run(
        service.should_auto_compact(db, session_id=session.id, threshold_tokens=4000)
    )
    result = asyncio.run(
        service.auto_compact_if_needed(db, session_id=session.id, threshold_tokens=4000)
    )
    assert result.compacted
    assert len(conversation_messages(db)) == 14
    assert len(db.storage[SessionSummary]) == 1
    assert not asyncio.run(
        service.should_auto_compact(db, session_id=session.id, threshold_tokens=4000)
    )
    db.add(
        Message(
            session_id=session.id,
            role="assistant",
            content="new response",
            metadata_json={"provider_usage": {"usage": {"input_tokens": 4500}}},
        )
    )
    assert asyncio.run(
        service.should_auto_compact(db, session_id=session.id, threshold_tokens=4000)
    )
