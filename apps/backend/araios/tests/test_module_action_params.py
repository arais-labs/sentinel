def _create_tool_module(client, admin_headers, *, name: str) -> None:
    resp = client.post(
        "/api/modules",
        json={
            "name": name,
            "label": name.title(),
            "type": "tool",
            "actions": [
                {
                    "id": "echo",
                    "label": "Echo",
                    "description": "Echo channel",
                    "params": [{"key": "channel", "type": "text", "required": True}],
                    "code": (
                        "channel = params.get('channel')\n"
                        "if not channel:\n"
                        "  result = {'ok': False, 'error': 'missing channel'}\n"
                        "else:\n"
                        "  result = {'ok': True, 'channel': channel}\n"
                    ),
                }
            ],
        },
        headers=admin_headers,
    )
    assert resp.status_code == 201


def test_tool_action_accepts_wrapped_and_flat_params(client, admin_headers, agent_headers):
    _create_tool_module(client, admin_headers, name="tool_echo_params")

    wrapped = client.post(
        "/api/modules/tool_echo_params/action/echo",
        json={"params": {"channel": "C123"}},
        headers=agent_headers,
    )
    assert wrapped.status_code == 200
    assert wrapped.json()["ok"] is True
    assert wrapped.json()["channel"] == "C123"

    flat = client.post(
        "/api/modules/tool_echo_params/action/echo",
        json={"channel": "C456"},
        headers=agent_headers,
    )
    assert flat.status_code == 200
    assert flat.json()["ok"] is True
    assert flat.json()["channel"] == "C456"


def test_tool_action_approval_exec_uses_wrapped_params(client, admin_headers, agent_headers):
    _create_tool_module(client, admin_headers, name="tool_echo_approval")

    set_perm = client.patch(
        "/api/permissions/tool_echo_approval.echo",
        json={"level": "approval"},
        headers=admin_headers,
    )
    assert set_perm.status_code == 200

    call = client.post(
        "/api/modules/tool_echo_approval/action/echo",
        json={"params": {"channel": "C777"}},
        headers=agent_headers,
    )
    assert call.status_code == 202
    approval_id = call.json()["detail"]["approval"]["id"]

    approved = client.post(f"/api/approvals/{approval_id}/approve", headers=admin_headers)
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
