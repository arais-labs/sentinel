from app.services.tools.approval import approval_waiters


def test_rejected_status_message_is_user_rejected_action() -> None:
    assert approval_waiters._status_message("rejected", None) == "User rejected action."
