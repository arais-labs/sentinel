from __future__ import annotations

from .handlers import (
    ALLOWED_DOCUMENT_COMMANDS,
    handle_create,
    handle_delete,
    handle_get,
    handle_list,
    handle_run,
    handle_update,
)
from .module import MODULE

__all__ = [
    "ALLOWED_DOCUMENT_COMMANDS",
    "MODULE",
    "handle_create",
    "handle_delete",
    "handle_get",
    "handle_list",
    "handle_run",
    "handle_update",
]
