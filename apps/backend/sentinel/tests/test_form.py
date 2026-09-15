import json
from uuid import uuid4

import pytest

from app.models import Message
from app.services.modules.builtins.form.contract import (
    Form,
    FormResponse,
    format_answers,
    validate_response,
)
from app.services.modules.builtins.form.module import MODULE, present
from app.services.modules.tool_adapter import build_module_tools
from tests.fake_db import FakeDB


def tree():
    return Form.model_validate(
        {
            "title": "Direction",
            "questions": [
                {"id": "1", "question": "Choose", "options": [{"id": "a", "label": "Suggested"}]},
                {"id": "2", "question": "Next root"},
                {"id": "1a", "parent_id": "1", "question": "Detail"},
            ],
        }
    )


@pytest.mark.parametrize("choice,text", [(None, "My own answer"), ("a", ""), ("a", "With changes")])
def test_choices_and_arbitrary_text(choice, text):
    response = FormResponse(
        form_id="f",
        answers=[
            {"question_id": "1", "option_id": choice, "text": text},
            {"question_id": "1a", "text": "Anything"},
            {"question_id": "2", "text": "More"},
        ],
    )
    result = format_answers(tree(), response)
    assert "Anything" in result
    if text:
        assert text in result
    if choice:
        assert "Suggested" in result


def test_dismiss_does_not_accept_defaults_or_partial_answers():
    result = format_answers(tree(), FormResponse(form_id="f", status="dismissed"))
    assert "No answers were submitted" in result
    with pytest.raises(ValueError):
        format_answers(
            tree(),
            FormResponse(
                form_id="f", status="dismissed", answers=[{"question_id": "1", "option_id": "a"}]
            ),
        )


def test_depth_first_and_deep_trees():
    assert [q.id for q in tree().ordered()] == ["1", "1a", "2"]
    form = Form(
        title="Deep",
        questions=[
            {"id": str(i), "parent_id": str(i - 1) if i else None, "question": "Detail"}
            for i in range(150)
        ],
    )
    assert len(form.ordered()) == 150


@pytest.mark.parametrize(
    "questions",
    [
        [{"id": "a", "parent_id": "b", "question": "Missing"}],
        [{"id": "a", "parent_id": "a", "question": "Cycle"}],
        [{"id": "a", "question": "One"}, {"id": "a", "question": "Duplicate"}],
    ],
)
def test_invalid_trees(questions):
    with pytest.raises(ValueError):
        Form(title="Invalid", questions=questions)


def test_incomplete_answers_rejected():
    with pytest.raises(ValueError):
        format_answers(tree(), FormResponse(form_id="f"))


def test_published_tool_schema_has_no_dangling_references():
    schema = build_module_tools(
        MODULE,
    )[0].parameters_schema
    assert "$ref" not in json.dumps(schema)
    assert (
        schema["properties"]["questions"]["items"]["properties"]["options"]["items"]["properties"][
            "label"
        ]["type"]
        == "string"
    )


@pytest.mark.asyncio
async def test_dismissal_scoped_to_session_and_cannot_repeat():
    db = FakeDB()
    session = uuid4()
    form = await present(tree().model_dump())
    db.add(
        Message(session_id=session, role="tool_result", tool_name="form", content=json.dumps(form))
    )
    payload = {"form_id": form["form_id"], "status": "dismissed"}
    with pytest.raises(ValueError, match="not found"):
        await validate_response(db, uuid4(), payload)
    content, metadata = await validate_response(db, session, payload)
    db.add(
        Message(
            session_id=session,
            role="user",
            content=content,
            metadata_json={"form_response": metadata},
        )
    )
    with pytest.raises(ValueError, match="resolved"):
        await validate_response(db, session, payload)


@pytest.mark.asyncio
async def test_waiting_status_survives_reading_and_clears_on_resolution():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base
    from app.services.modules.builtins.form.contract import pending_form_sessions

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine)() as db:
            session = uuid4()
            form = await present(tree().model_dump())
            db.add(
                Message(
                    session_id=session,
                    role="tool_result",
                    tool_name="form",
                    content=json.dumps(form),
                )
            )
            assert await pending_form_sessions(db, [session]) == {session: form["form_id"]}
            # Ordinary conversation/read activity does not resolve a question.
            db.add(Message(session_id=session, role="user", content="Hello", metadata_json={}))
            assert await pending_form_sessions(db, [session]) == {session: form["form_id"]}
            db.add(
                Message(
                    session_id=session,
                    role="user",
                    content="Dismissed",
                    metadata_json={
                        "form_response": {
                            "form_id": form["form_id"],
                            "status": "dismissed",
                            "answers": [],
                        }
                    },
                )
            )
            assert await pending_form_sessions(db, [session]) == {}
            next_form = await present(tree().model_dump())
            db.add(
                Message(
                    session_id=session,
                    role="tool_result",
                    tool_name="form",
                    content=json.dumps(next_form),
                )
            )
            assert await pending_form_sessions(db, [session]) == {session: next_form["form_id"]}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_completion_ids_ignore_tool_calls_and_keep_latest_final():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base, Session
    from app.services.sessions.agent_run_registry import AgentRunRegistry
    from app.services.sessions.service import SessionService

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine)() as db:
            session = Session(id=uuid4(), user_id="local")
            service = SessionService(run_registry=AgentRunRegistry())
            assert await service.completion_ids(db, [session]) == {}
            intermediate = Message(
                session_id=session.id,
                role="assistant",
                content="Calling a tool",
                metadata_json={"stop_reason": "tool_use"},
            )
            db.add(intermediate)
            assert await service.completion_ids(db, [session]) == {}
            final = Message(
                session_id=session.id,
                role="assistant",
                content="Done",
                metadata_json={"stop_reason": "stop"},
            )
            db.add(final)
            await db.flush()
            assert await service.completion_ids(db, [session]) == {session.id: str(final.id)}
            db.add(
                Message(session_id=session.id, role="tool_result", tool_name="form", content="{}")
            )
            assert await service.completion_ids(db, [session]) == {session.id: str(final.id)}
    finally:
        await engine.dispose()
