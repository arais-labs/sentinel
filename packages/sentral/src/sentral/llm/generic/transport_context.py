"""Task-local chat identity for provider transport recovery."""

from contextlib import contextmanager
from contextvars import ContextVar

transport_session: ContextVar[str | None] = ContextVar("transport_session", default=None)


@contextmanager
def provider_transport_session(session_id: str):
    token = transport_session.set(session_id)
    try:
        yield
    finally:
        transport_session.reset(token)
