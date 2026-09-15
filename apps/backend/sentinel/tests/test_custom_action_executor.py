import asyncio

import httpx
import pytest

from app.services.modules import action_executor as executor


def test_async_http_and_coroutine_result(monkeypatch):
    real_client = httpx.AsyncClient
    calls = []

    async def respond(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={"items": [1, 2]})

    monkeypatch.setattr(
        executor.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw),
    )
    for code in [
        "response = await http.get('https://api.example.com/items')\nresponse.raise_for_status()\nresult = response.json()",
        "async def fetch():\n response = await http.get('https://api.example.com/items')\n return response.json()\nresult = fetch()",
    ]:
        assert asyncio.run(executor.execute_action(code, {})) == {"items": [1, 2]}
    assert len(calls) == 2


@pytest.mark.parametrize(
    "code, expected",
    [
        ("result = {'value': 3}", {"value": 3}),
        ("value = 3", {"ok": True}),
        ("result = None", {"ok": False, "error": "Action result must be a dictionary"}),
        ("raise ValueError('failed')", {"ok": False, "error": "failed"}),
        (
            "async def fail():\n raise ValueError('async failed')\nresult = fail()",
            {"ok": False, "error": "async failed"},
        ),
    ],
)
def test_result_contract(code, expected):
    assert asyncio.run(executor.execute_action(code, {})) == expected


def test_invalid_edit_is_atomic():
    from types import SimpleNamespace

    from app.schemas.modules import EditModuleRequest
    from app.services.modules.updates import fold_ops_into_delta

    mod = SimpleNamespace(
        actions=[{"id": "fetch", "code": "result = {}"}], fields=[], secrets=[], fields_config={}
    )
    request = EditModuleRequest.model_validate(
        {
            "name": "example",
            "ops": [
                {"op": "patch_action", "id": "fetch", "set": {"code": "if broken"}},
            ],
        }
    )
    with pytest.raises(ValueError, match="fetch.*line 1"):
        fold_ops_into_delta(mod, request)
    assert mod.actions == [{"id": "fetch", "code": "result = {}"}]


def test_validation_does_not_execute():
    from app.services.modules.updates import validate_action_updates

    validate_action_updates(
        [
            {
                "id": "fetch",
                "code": "raise RuntimeError('must not execute')\nresult = await http.get('https://api.example.com')",
            }
        ]
    )
    with pytest.raises(ValueError, match="fetch.*line 1"):
        validate_action_updates([{"id": "fetch", "code": "if broken"}])
