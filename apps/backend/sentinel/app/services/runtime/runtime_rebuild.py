from __future__ import annotations

from typing import Any


class RuntimeRebuildService:
    """Rebuild per-instance runtime context after mutable settings change."""

    async def rebuild_request_runtime_support(self, request: Any) -> None:
        from app.services.instance_runtime_context import (
            InstanceRuntimeContext,
            instance_runtime_context_registry,
        )

        context = getattr(request.state, "instance_runtime_context", None)
        if not isinstance(context, InstanceRuntimeContext):
            raise RuntimeError(
                "rebuild_request_runtime_support requires an instance-scoped request "
                "(request.state.instance_runtime_context is missing)."
            )
        await instance_runtime_context_registry.rebuild_context(
            app_state=request.app.state,
            context=context,
        )
