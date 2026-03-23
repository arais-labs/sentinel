from __future__ import annotations

from typing import Any


def extract_approval_metadata_from_tool_result(
    *,
    tool_name: str,
    result: Any,
) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None

    generic = _extract_generic_approval(result)
    if generic is not None:
        return generic

    return None


def _extract_generic_approval(result: dict[str, Any]) -> dict[str, Any] | None:
    approval = result.get("approval")
    if not isinstance(approval, dict):
        return None
    provider = str(approval.get("provider") or "").strip()
    approval_id = str(approval.get("approval_id") or "").strip()
    if not provider or not approval_id:
        return None
    status = str(approval.get("status") or "pending").strip() or "pending"
    pending = bool(approval.get("pending")) if "pending" in approval else status == "pending"
    can_resolve = (
        bool(approval.get("can_resolve"))
        if "can_resolve" in approval
        else status == "pending"
    )
    payload: dict[str, Any] = {
        "provider": provider,
        "approval_id": approval_id,
        "status": status,
        "pending": pending,
        "can_resolve": can_resolve,
        "label": approval.get("label") or f"{provider} approval",
    }
    for key in ("action", "description", "decision_note", "decision_by"):
        if key in approval:
            payload[key] = approval.get(key)
    return payload
