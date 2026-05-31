from app.models import Message, Session
from app.services.sessions.context_usage import estimate_db_message_tokens


def test_estimate_db_message_tokens_counts_assistant_tool_calls_when_text_empty():
    session = Session(user_id="dev-admin", status="active", title="tokens")
    message = Message(
        session_id=session.id,
        role="assistant",
        content="",
        metadata_json={
            "tool_calls": [
                {
                    "id": "toolu_1",
                    "name": "module_manager",
                    "arguments": {"command": "list_modules"},
                }
            ]
        },
    )

    assert estimate_db_message_tokens(message) > 0


def test_estimate_db_message_tokens_preserves_structural_tool_result_rows():
    session = Session(user_id="dev-admin", status="active", title="tokens")
    message = Message(
        session_id=session.id,
        role="tool_result",
        content="",
        tool_call_id="toolu_1",
        tool_name="module_manager",
        metadata_json={},
    )

    assert estimate_db_message_tokens(message) == 1
