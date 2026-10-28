import asyncio
from types import SimpleNamespace

import asyncssh

from app.services.workspaces.machines.remote.worker import RuntimeUnavailable, unreachable


def test_unreachable_names_the_machine_and_what_to_check():
    machine = SimpleNamespace(name="example-mac", host="192.0.2.10")
    for exc, words in [
        (ConnectionRefusedError(61, "Connection refused"), "Remote Login"),
        (asyncio.TimeoutError(), "did not answer"),
        (asyncssh.PermissionDenied("denied"), "SSH key"),
        (OSError(65, "No route to host"), "No route to host"),
    ]:
        error = unreachable(machine, exc)
        assert isinstance(error, RuntimeUnavailable)
        assert str(error).startswith("Can't reach example-mac (192.0.2.10): ")
        assert words in str(error)


def test_unreachable_worker_is_a_503_with_its_message():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.middleware.error_handler import register_error_handlers

    app = FastAPI()
    register_error_handlers(app)

    @app.get("/live")
    async def live():
        raise RuntimeUnavailable("Can't reach example-mac (192.0.2.10): it refused the connection.")

    response = TestClient(app).get("/live")
    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "machine_unavailable",
        "message": "Can't reach example-mac (192.0.2.10): it refused the connection.",
    }
