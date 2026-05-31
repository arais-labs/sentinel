from app.main import app


def test_app_data_routes_are_instance_scoped():
    paths = {getattr(route, "path", "") for route in app.routes}

    assert "/api/v1/instances/{instance_name}/sessions" in paths
    assert "/api/v1/instances/{instance_name}/memory" in paths
    assert "/api/v1/instances/{instance_name}/settings/api-keys" in paths
    assert "/ws/instances/{instance_name}/sessions/{id}/stream" in paths
    assert "/api/v1/instances/{instance_name}/modules" in paths

    assert "/api/v1/sessions" not in paths
    assert "/api/v1/memory" not in paths
    assert "/api/v1/settings/api-keys" not in paths
    assert "/ws/sessions/{id}/stream" not in paths
    assert "/api/modules" not in paths
    # araios is now under /api/v1/instances/{name}/... — no more legacy /api/ mount
    assert "/api/instances/{instance_name}/modules" not in paths
