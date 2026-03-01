from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from typing import Any


_SESSION_LOG_CONTEXT: ContextVar[str] = ContextVar("session_log_context", default="-")


class SessionLogFilter(logging.Filter):
    """Inject session context into all log records for easier traceability."""

    def filter(self, record: logging.LogRecord) -> bool:
        session_id = getattr(record, "session_id", None)
        if not isinstance(session_id, str) or not session_id:
            record.session_id = _SESSION_LOG_CONTEXT.get("-")
        return True


def set_log_session(session_id: Any | None) -> Token[str]:
    if session_id is None:
        return _SESSION_LOG_CONTEXT.set("-")
    value = str(session_id).strip()
    return _SESSION_LOG_CONTEXT.set(value or "-")


def reset_log_session(token: Token[str]) -> None:
    _SESSION_LOG_CONTEXT.reset(token)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s [session=%(session_id)s]: %(message)s",
    )
    _attach_session_filter()


def _attach_session_filter() -> None:
    session_filter = SessionLogFilter()
    roots = [
        logging.getLogger(),
        logging.getLogger("uvicorn"),
        logging.getLogger("uvicorn.error"),
        logging.getLogger("uvicorn.access"),
    ]
    for logger in roots:
        for handler in logger.handlers:
            if any(isinstance(existing, SessionLogFilter) for existing in handler.filters):
                continue
            handler.addFilter(session_filter)
