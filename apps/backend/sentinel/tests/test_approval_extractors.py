from __future__ import annotations

from app.services.approvals.extractors import extract_approval_metadata_from_tool_result


def test_extracts_araios_approval_from_202_response():
    result = {
        "status_code": 202,
        "body": {
            "detail": {
                "message": "Action requires approval",
                "approval": {
                    "id": "apr_123",
                    "status": "pending",
                    "action": "tasks.delete",
                    "description": "Agent requested task delete",
                },
            }
        },
    }

    approval = extract_approval_metadata_from_tool_result(tool_name="modules_discovery", result=result)

    assert isinstance(approval, dict)
    assert approval["provider"] == "araios"
    assert approval["approval_id"] == "apr_123"
    assert approval["pending"] is True
    assert approval["can_resolve"] is True


def test_extracts_git_approval_from_git_exec_result():
    result = {
        "command": "git push origin main",
        "approval": {
            "provider": "git",
            "approval_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
            "status": "approved",
            "decision_note": "ok",
        },
    }

    approval = extract_approval_metadata_from_tool_result(tool_name="git_exec", result=result)

    assert isinstance(approval, dict)
    assert approval["provider"] == "git"
    assert approval["approval_id"] == "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
    assert approval["status"] == "approved"
    assert approval["pending"] is False
    assert approval["match_key"] == "git push origin main"
