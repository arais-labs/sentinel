from __future__ import annotations

from typing import Any

from app.services.approvals.tool_match import normalize_command


def extract_approval_metadata_from_tool_result(
    *,
    tool_name: str,
    result: Any,
) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None

    araios = _extract_araios_approval(result)
    if araios is not None:
        return araios

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
    match_key = approval.get("match_key")
    if isinstance(match_key, str) and match_key.strip():
        normalized_match = match_key.strip()
    else:
        command = approval.get("command") or result.get("command")
        normalized_match = normalize_command(command) if isinstance(command, str) and command.strip() else None

    payload: dict[str, Any] = {
        "provider": provider,
        "approval_id": approval_id,
        "status": status,
        "pending": pending,
        "can_resolve": can_resolve,
        "label": approval.get("label") or f"{provider} approval",
    }
    if normalized_match:
        payload["match_key"] = normalized_match
    for key in ("action", "description", "decision_note", "decision_by"):
        if key in approval:
            payload[key] = approval.get(key)
    return payload


def _extract_araios_approval(result: dict[str, Any]) -> dict[str, Any] | None:
    status_code = result.get("status_code")
    body = result.get("body")
    if status_code != 202 or not isinstance(body, dict):
        return None

    # Try standard format: body.detail.approval
    detail = body.get("detail")
    if not isinstance(detail, dict):
        # Try error-envelope format: body.error.details.approval
        error_obj = body.get("error")
        if isinstance(error_obj, dict):
            detail = error_obj.get("details")
    if not isinstance(detail, dict):
        return None

    approval = detail.get("approval")
    if not isinstance(approval, dict):
        return None

    approval_id = str(approval.get("id") or "").strip()
    status = str(approval.get("status") or "pending").strip() or "pending"
    action = str(approval.get("action") or "").strip() or None
    description = str(approval.get("description") or "").strip() or None
    if not approval_id:
        return None

    return {
        "provider": "araios",
        "approval_id": approval_id,
        "status": status,
        "pending": status == "pending",
        "can_resolve": status == "pending",
        "label": "AraiOS approval",
        "action": action,
        "description": description,
    }
