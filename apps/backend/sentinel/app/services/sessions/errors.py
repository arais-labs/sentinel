from __future__ import annotations


class SessionServiceError(Exception):
    """Base error for session service workflows."""


class SessionNotFoundError(SessionServiceError):
    """Requested session does not exist or is not owned by the caller."""


class MessageNotFoundError(SessionServiceError):
    """Requested message does not exist in the target session."""


class MainSessionDeletionError(SessionServiceError):
    """Main session cannot be deleted."""


class MainSessionTargetInvalidError(SessionServiceError):
    """Requested main session target is invalid."""


class AgentLoopUnavailableError(SessionServiceError):
    """Agent loop/provider is not configured."""


class ChatPayloadRequiredError(SessionServiceError):
    """Chat requires text and/or attachments."""


class RuntimePathInvalidError(SessionServiceError):
    """Requested runtime path is invalid."""


class RuntimePathNotFoundError(SessionServiceError):
    """Requested runtime path does not exist."""
