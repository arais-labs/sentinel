from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from typing import Any


_SESSION_LOG_CONTEXT: ContextVar[str] = ContextVar("session_log_context", default="-")
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s [session=%(session_id)s]: %(message)s"
_DEFAULT_ROOT_LEVEL = "INFO"
_DEFAULT_LOGGER_LEVELS: dict[str, str] = {
    "httpx": "WARNING",
    "httpcore": "WARNING",
    "uvicorn.access": "INFO",
    "app.services.agent": "INFO",
    "app.services.llm": "INFO",
}
_RUNTIME_LOGGER_OVERRIDES: dict[str, str] = {}


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
        level=getattr(logging, _DEFAULT_ROOT_LEVEL),
        format=_LOG_FORMAT,
    )
    _attach_session_filter()
    apply_logging_config()


def apply_logging_config() -> None:
    _set_logger_level(None, _DEFAULT_ROOT_LEVEL)
    for logger_name, level_name in _DEFAULT_LOGGER_LEVELS.items():
        _set_logger_level(logger_name, level_name)
    for logger_name, level_name in _RUNTIME_LOGGER_OVERRIDES.items():
        _set_logger_level(logger_name, level_name)


def get_logging_config_snapshot() -> dict[str, Any]:
    names = set(_DEFAULT_LOGGER_LEVELS) | set(_RUNTIME_LOGGER_OVERRIDES)
    effective = {
        name: logging.getLevelName(logging.getLogger(name).getEffectiveLevel())
        for name in sorted(names)
    }
    return {
        "root_level": logging.getLevelName(logging.getLogger().getEffectiveLevel()),
        "default_root_level": _DEFAULT_ROOT_LEVEL,
        "default_logger_levels": dict(sorted(_DEFAULT_LOGGER_LEVELS.items())),
        "runtime_overrides": dict(sorted(_RUNTIME_LOGGER_OVERRIDES.items())),
        "effective_logger_levels": effective,
    }


def set_runtime_logger_override(logger_name: str, level_name: str) -> dict[str, Any]:
    normalized_logger = _normalize_logger_name(logger_name)
    normalized_level = _normalize_level_name(level_name)
    _RUNTIME_LOGGER_OVERRIDES[normalized_logger] = normalized_level
    apply_logging_config()
    return get_logging_config_snapshot()


def clear_runtime_logger_override(logger_name: str) -> dict[str, Any]:
    normalized_logger = _normalize_logger_name(logger_name)
    _RUNTIME_LOGGER_OVERRIDES.pop(normalized_logger, None)
    apply_logging_config()
    return get_logging_config_snapshot()


def clear_all_runtime_logger_overrides() -> dict[str, Any]:
    _RUNTIME_LOGGER_OVERRIDES.clear()
    apply_logging_config()
    return get_logging_config_snapshot()


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


def _set_logger_level(logger_name: str | None, level_name: str) -> None:
    logger = logging.getLogger() if logger_name in {None, ""} else logging.getLogger(logger_name)
    logger.setLevel(getattr(logging, _normalize_level_name(level_name)))


def _normalize_level_name(value: str) -> str:
    normalized = str(value).strip().upper()
    allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}
    if normalized not in allowed:
        raise ValueError(f"Unsupported log level: {value}")
    return normalized


def _normalize_logger_name(value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError("Logger name must be a non-empty string")
    return normalized
