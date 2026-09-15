from app.services.modules.definitions import ActionDefinition, ModuleDefinition
from app.services.modules.runtime_services import get_ws_manager
from sentral.errors import ToolValidationError


def handler(action):
    async def execute(payload, runtime):
        if runtime.session_id is None:
            raise ToolValidationError("Layout requires an active session")
        manager = get_ws_manager()
        if manager is None:
            raise ToolValidationError("Sentinel layout connection is unavailable")
        operations = payload.get("operations", [])
        if action == "apply" and (
            not isinstance(operations, list) or not 1 <= len(operations) <= 20
        ):
            raise ToolValidationError("Supply between 1 and 20 layout operations")
        try:
            return await manager.request_layout(
                str(runtime.session_id), {"action": action, "operations": operations}
            )
        except RuntimeError as exc:
            raise ToolValidationError(str(exc)) from exc

    return execute


operation = {
    "type": "object",
    "additionalProperties": False,
    "required": ["operation"],
    "properties": {
        "operation": {
            "type": "string",
            "enum": ["open", "move", "resize", "close", "focus", "maximize", "restore"],
        },
        "pane_id": {
            "type": "string",
            "description": "Pane ID from inspect; required except open and restore.",
        },
        "tab_id": {
            "type": "string",
            "description": "View type from inspect.available_views; required for open. Each view opens at most once.",
        },
        "reference_pane_id": {
            "type": "string",
            "description": "Place beside this pane; required for move, optional for open.",
        },
        "direction": {"type": "string", "enum": ["left", "right", "above", "below"]},
        "width": {
            "type": "integer",
            "minimum": 160,
            "description": "Requested width in pixels; constrained by available space.",
        },
        "height": {
            "type": "integer",
            "minimum": 120,
            "description": "Requested height in pixels; constrained by available space.",
        },
    },
}

MODULE = ModuleDefinition(
    name="session_layout",
    label="Session Layout",
    icon="layout-grid",
    system=True,
    grouped_tool=True,
    description="Organize only the current session's Sentinel panes, not desktop windows or terminal process splits. Inspect returns pane IDs, view labels, positions and sizes, never pane contents. Offer to organize and wait for the user's agreement before apply/undo unless they already requested layout changes. Once authorized, arrange without asking about each pane. Inspect first; preserve useful views and respect available space. Closing a pane hides its view, not its underlying terminal or session. Undo restores the previous arrangement. The session must be displayed in one Sentinel window.",
    actions=[
        ActionDefinition(
            id=action,
            label=label,
            description=description,
            handler=handler(action),
            requires_runtime_context=True,
            parameters_schema=schema,
        )
        for action, label, description, schema in [
            (
                "inspect",
                "Inspect Layout",
                "Inspect layout metadata and available view types.",
                {"type": "object", "additionalProperties": False, "properties": {}},
            ),
            (
                "apply",
                "Arrange Layout",
                "Apply up to 20 operations after user agreement. Returns actual sizes and positions. Failed batches roll back.",
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["operations"],
                    "properties": {
                        "operations": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 20,
                            "items": operation,
                        }
                    },
                },
            ),
            (
                "undo",
                "Undo Layout",
                "Restore the previous agent layout change after user agreement; no file or process changes.",
                {"type": "object", "additionalProperties": False, "properties": {}},
            ),
        ]
    ],
)
