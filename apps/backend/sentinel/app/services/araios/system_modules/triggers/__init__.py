from __future__ import annotations

from .handlers import handle_create, handle_delete, handle_list, handle_update
from .module import MODULE

__all__ = [
    "MODULE",
    "handle_create",
    "handle_delete",
    "handle_list",
    "handle_update",
]
