from starlette.routing import Match

from app.main import app


def test_app_data_routes_are_instance_scoped():
    # Included routers are lazy in current FastAPI; OpenAPI exposes their HTTP paths.
    paths = set(app.openapi()["paths"])

    assert "/api/v1/instances/{instance_name}/sessions" in paths
    assert "/api/v1/instances/{instance_name}/memory" in paths
    assert "/api/v1/instances/{instance_name}/settings/api-keys" in paths
    assert "/api/v1/instances/{instance_name}/modules" in paths

    assert "/api/v1/sessions" not in paths
    assert "/api/v1/memory" not in paths
    assert "/api/v1/settings/api-keys" not in paths
    assert "/api/modules" not in paths
    # Modules are mounted under /api/v1/instances/{name}/... — no more legacy /api/ mount
    assert "/api/instances/{instance_name}/modules" not in paths

    # WebSockets aren't in OpenAPI. Exercise routing with concrete ASGI paths.
    def matches_websocket(path):
        scope = {"type": "websocket", "path": path, "root_path": ""}
        return any(route.matches(scope)[0] == Match.FULL for route in app.routes)

    assert matches_websocket("/ws/instances/test/sessions/123/stream")
    assert not matches_websocket("/ws/sessions/123/stream")
