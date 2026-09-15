from dataclasses import replace
from unittest.mock import patch

from starlette.requests import HTTPConnection

from app.dependencies import get_connection_instance_runtime_context
from tests.fake_db import FakeDB
from tests.helpers import make_fake_instance_context


def test_open_connection_picks_up_rebuilt_runtime_without_rebuilding_or_io():
    old = make_fake_instance_context(app_db=FakeDB(), agent_runtime_support=object())
    current = replace(old, agent_runtime_support=object())
    connection = HTTPConnection({"type": "websocket", "state": {"instance_runtime_context": old}})
    with patch("app.dependencies.instance_runtime_context_registry") as registry:
        registry.get.return_value = current
        assert get_connection_instance_runtime_context(connection) is current
        assert connection.state.instance_runtime_context is current
        assert get_connection_instance_runtime_context(connection) is current
        assert registry.get.call_count == 2
        registry.get_or_create.assert_not_called()
        registry.rebuild_context.assert_not_called()
    # A run which already captured the previous support is not rewritten.
    assert old.agent_runtime_support is not current.agent_runtime_support


def test_lookup_does_not_cross_instance_database_boundary():
    old = make_fake_instance_context(app_db=FakeDB())
    connection = HTTPConnection({"type": "websocket", "state": {"instance_runtime_context": old}})
    with patch("app.dependencies.instance_runtime_context_registry") as registry:
        registry.get.return_value = replace(old, database_name="different")
        assert get_connection_instance_runtime_context(connection) is old
