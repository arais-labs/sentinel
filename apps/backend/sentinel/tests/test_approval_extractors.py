from __future__ import annotations

from app.services.tools.approval.extractors import extract_approval_metadata_from_tool_result


def test_extracts_git_approval_from_git_exec_result():
    result = {
        "command": "git push origin main",
        "approval": {
            "provider": "git_exec",
            "approval_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
            "status": "approved",
            "decision_note": "ok",
        },
    }

    approval = extract_approval_metadata_from_tool_result(tool_name="git_exec", result=result)

    assert isinstance(approval, dict)
    assert approval["provider"] == "git_exec"
    assert approval["approval_id"] == "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
    assert approval["status"] == "approved"
    assert approval["pending"] is False
