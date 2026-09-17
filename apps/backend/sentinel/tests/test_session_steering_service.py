from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.models import Message
from app.schemas.sessions import SteeringRequest
from app.services.sessions.agent_run_registry import AgentRunRegistry
from app.services.sessions.errors import (
    AgentRuntimeUnavailableError,
    SteeringConflictError,
    SteeringValidationError,
)
from app.services.sessions.service import SessionService
from app.services.sessions.steering import enqueue_session_steering
from tests.fake_db import FakeDB
from tests.test_runtime_support import _new_session


def steering_fixture():
    db = FakeDB()
    session = _new_session(db, user_id="local")
    registry = AgentRunRegistry()
    registry.notify_idle_interjections = AsyncMock()
    dependencies = dict(
        session_id=session.id,
        user_id="local",
        sessions=SessionService(run_registry=registry),
        provider=SimpleNamespace(model_context=Mock(return_value={})),
        registry=registry,
        manager=SimpleNamespace(broadcast_message_ack=AsyncMock()),
    )
    return db, dependencies


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata", [None, {"source": "voice", "notice": {"title": "Voice instruction"}}]
)
async def test_steering_service_reuses_delivery_without_http_request(metadata):
    db, dependencies = steering_fixture()
    payload = SteeringRequest(message_id=uuid4(), content="Continue the work")
    first = await enqueue_session_steering(
        db, payload=payload, ingress_metadata=metadata, **dependencies
    )
    again = await enqueue_session_steering(
        db, payload=payload, ingress_metadata=metadata, **dependencies
    )
    assert isinstance(first, Message)
    assert first.id == again.id == payload.message_id
    assert first.role == "user"
    assert first.metadata_json["source"] == ("voice" if metadata else "web")
    assert first.metadata_json["steering"] == "pending"
    assert first.metadata_json.get("notice") == (metadata or {}).get("notice")
    queued = dependencies["registry"].peek_interjections(str(dependencies["session_id"]))
    assert len(queued) == 1
    assert queued[0].metadata["source"] == first.metadata_json["source"]
    assert len(db.storage[Message]) == 1
    dependencies["registry"].notify_idle_interjections.assert_awaited_with(
        str(dependencies["session_id"])
    )


@pytest.mark.asyncio
async def test_steering_service_raises_domain_errors_before_queueing():
    db, dependencies = steering_fixture()
    payload = SteeringRequest(message_id=uuid4(), content="Continue")
    with pytest.raises(AgentRuntimeUnavailableError):
        await enqueue_session_steering(db, payload=payload, **{**dependencies, "provider": None})
    with pytest.raises(SteeringValidationError, match="Invalid steering"):
        await enqueue_session_steering(
            db, payload=SteeringRequest(message_id=uuid4(), content=""), **dependencies
        )
    dependencies["provider"].model_context.side_effect = ValueError(
        "Selected provider is not configured"
    )
    with pytest.raises(SteeringValidationError, match="Selected provider"):
        await enqueue_session_steering(db, payload=payload, **dependencies)
    assert not db.storage[Message]
    dependencies["registry"].notify_idle_interjections.assert_not_awaited()


@pytest.mark.asyncio
async def test_steering_service_rejects_message_ids_owned_by_another_session():
    db, dependencies = steering_fixture()
    payload = SteeringRequest(message_id=uuid4(), content="Continue")
    existing = Message(id=payload.message_id, session_id=uuid4(), role="user", content="Other chat")
    existing.metadata_json = {"steering_id": str(payload.message_id), "steering": "pending"}
    db.add(existing)
    with pytest.raises(SteeringConflictError):
        await enqueue_session_steering(db, payload=payload, **dependencies)
    dependencies["manager"].broadcast_message_ack.assert_not_awaited()
    dependencies["registry"].notify_idle_interjections.assert_not_awaited()


@pytest.mark.asyncio
async def test_stop_session_cancels_pending_steering_before_stopping_generation():
    db, dependencies = steering_fixture()
    message = await enqueue_session_steering(
        db, payload=SteeringRequest(message_id=uuid4(), content="Continue"), **dependencies
    )
    sessions = dependencies["sessions"]

    async def stop_generation(*args, **kwargs):
        assert message.metadata_json["steering"] == "cancelled"
        assert dependencies["registry"].peek_interjections(str(message.session_id)) == []
        return True

    sessions.stop_generation = AsyncMock(side_effect=stop_generation)
    assert await sessions.stop_session(db, session_id=message.session_id, user_id="local")
    sessions.stop_generation.assert_awaited_once_with(
        db, session_id=message.session_id, user_id="local"
    )
