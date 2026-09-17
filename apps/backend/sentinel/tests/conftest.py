from __future__ import annotations

import os
import tempfile

import pytest
import pytest_asyncio

_test_storage = tempfile.TemporaryDirectory(prefix="sentinel-tests-")
os.environ["SENTINEL_STORAGE_ROOT"] = _test_storage.name

# Shared pytest defaults so local test runs do not depend on shell-exported env vars.
os.environ["SENTINEL_DESKTOP_TOKEN"] = "test-desktop-transport-token"
os.environ["DATA_ENCRYPTION_KEY"] = "test-data-key-with-32-bytes-minimum"
os.environ["TOOL_FILE_READ_BASE_DIR"] = _test_storage.name
os.environ.pop("SENTINEL_WORKSPACE_RUNTIME_SOCKET", None)

# The runtime package has an import cycle that only resolves when the instance runtime
# context loads before module registries. Anchor that order for every test module.
import app.services.instance_runtime_context  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _fake_runtime_manager_db(monkeypatch):
    # The WS connect path probes runtime config via ssh_runtime's own module-level
    # ManagerSessionLocal, which bypasses dependency overrides and would open a real
    # database connection. Point it at an empty fake so the probe resolves "unconfigured".
    from app.services.runtime import ssh_runtime
    from tests.fake_db import FakeDB
    from tests.helpers import FakeSessionFactory

    monkeypatch.setattr(ssh_runtime, "ManagerSessionLocal", FakeSessionFactory(FakeDB()))
    from app import dependencies

    async def empty_instance_factory(instance_name):
        return FakeSessionFactory(FakeDB())

    monkeypatch.setattr(dependencies, "get_instance_session_factory", empty_instance_factory)


def pytest_addoption(parser):
    parser.addoption(
        "--live-cache",
        action="store_true",
        help="Verify provider cache hits using explicitly supplied test credentials",
    )
    parser.addoption(
        "--live-claude",
        action="store_true",
        help="Test Claude using SENTINEL_TEST_ANTHROPIC_TOKEN (incurs provider charges)",
    )

    parser.addoption(
        "--live-gemini",
        action="store_true",
        help="Run Gemini with dedicated SENTINEL_TEST_GEMINI credentials",
    )
    parser.addoption(
        "--live-workspace",
        action="store_true",
        help="Run integration tests against an explicitly supplied disposable workspace",
    )


@pytest_asyncio.fixture
async def container_transport(request):
    """Live VM regressions run against an explicitly supplied disposable workspace."""
    import os
    from uuid import UUID
    from app.services.runtime.container_transport import ContainerTransport

    if not request.config.getoption("--live-workspace"):
        pytest.skip("Opt in with --live-workspace and a disposable test workspace")
    workspace = os.environ.get("SENTINEL_TEST_WORKSPACE_ID")
    if not workspace:
        pytest.skip("requires the disposable workspace integration harness")
    transport = ContainerTransport(UUID(workspace), "/unused-existing-project", [])
    try:
        yield transport
    finally:
        await transport.close()


@pytest.fixture(autouse=True)
def _offline_network(request, monkeypatch):
    """Unit tests may use local servers, never live remote services by accident."""
    import ipaddress
    import socket

    live_provider = request.node.get_closest_marker("live_provider")
    if live_provider and request.config.getoption(live_provider.args[0]):
        return
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_resolve = socket.getaddrinfo

    def check(host):
        if host in {None, "localhost", b"localhost"}:
            return
        try:
            if ipaddress.ip_address(host).is_loopback:
                return
        except ValueError:
            pass
        raise AssertionError("External network disabled in offline tests; mock this boundary")

    def connect(sock, address):
        if sock.family != socket.AF_UNIX:
            check(address[0])
        return original_connect(sock, address)

    def connect_ex(sock, address):
        if sock.family != socket.AF_UNIX:
            check(address[0])
        return original_connect_ex(sock, address)

    def resolve(host, *args, **kwargs):
        check(host)
        return original_resolve(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", resolve)


def pytest_configure(config):
    if config.getoption("--live-workspace"):
        runtime_socket = os.environ.get("SENTINEL_TEST_WORKSPACE_RUNTIME_SOCKET")
        if runtime_socket:
            os.environ["SENTINEL_WORKSPACE_RUNTIME_SOCKET"] = runtime_socket
    config.addinivalue_line(
        "markers", "live_provider(option): explicitly opt-in provider integration"
    )


def pytest_collection_modifyitems(config, items):
    for item in items:
        live = item.get_closest_marker("live_provider")
        if live and not config.getoption(live.args[0]):
            item.add_marker(
                pytest.mark.skip(
                    reason=f"Opt in with {live.args[0]} and dedicated test credentials"
                )
            )
