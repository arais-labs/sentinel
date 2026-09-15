from __future__ import annotations

from typing import Any


class ToolValidationError(ValueError):
    def __init__(self, message: str, *, approval: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.approval = approval
