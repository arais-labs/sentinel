from __future__ import annotations


class SessionServiceError(Exception):
    """Base error for session service workflows."""


class SessionNotFoundError(SessionServiceError):
    """Requested session does not exist or is not owned by the caller."""


class MessageNotFoundError(SessionServiceError):
    """Requested message does not exist in the target session."""


class SessionWorkspaceCleanupError(SessionServiceError):
    """Runtime workspace cleanup failed before session deletion."""

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.detail = detail


class AgentRuntimeUnavailableError(SessionServiceError):
    """Agent runtime support/provider is not configured."""


class ChatPayloadRequiredError(SessionServiceError):
    """Chat requires text and/or attachments."""


class SessionRenameNotAllowedError(SessionServiceError):
    """Session title cannot be changed for this session type."""
