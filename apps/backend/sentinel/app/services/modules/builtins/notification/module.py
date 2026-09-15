from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.modules.definitions import ActionDefinition, ModuleDefinition
from app.services.notifications import Notification, publish_notification
from app.services.tools.registry import ToolRuntimeContext
from app.services.tools.runtime_context import require_session_id


class Alert(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=160, description="A short, specific alert title.")
    message: str = Field(
        min_length=1,
        max_length=4000,
        description="Why the user should pay attention and what action is needed.",
    )
    severity: Literal["info", "warning", "urgent"] = Field(
        default="info",
        description="Use urgent only when prompt user attention matters. Delivery respects the user's notification settings.",
    )
    key: str | None = Field(
        default=None,
        max_length=100,
        description="Reuse a short key to update the same alert in this session instead of adding duplicates.",
    )

    @field_validator("title", "message")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Enter nonempty text")
        return value.strip()


async def send(payload: dict, runtime: ToolRuntimeContext) -> dict:
    alert = Alert.model_validate(payload)
    session_id = str(require_session_id(runtime))
    if not runtime.instance_name:
        raise ValueError("Notification requires an instance context")
    item = await publish_notification(
        Notification(
            source="agent",
            title=alert.title,
            message=alert.message,
            severity=alert.severity,
            key=f"{runtime.instance_name}:{session_id}:{alert.key or alert.title}"[:200],
            target={"instanceName": runtime.instance_name, "sessionId": session_id},
        )
    )
    return {
        "notification_id": item["id"],
        "status": "delivered",
        "message": "Saved to the user's notification inbox. Banners and sounds follow their settings.",
    }


MODULE = ModuleDefinition(
    name="notification",
    label="Notifications",
    icon="bell",
    system=True,
    grouped_tool=True,
    description="Get the user's attention with a notification linked to this session. No workspace is needed.",
    actions=[
        ActionDefinition(
            id="send",
            label="Notify user",
            handler=send,
            requires_runtime_context=True,
            permission_default="allow",
            description="Send an immediate notification for a deadline, urgent finding, blocker, or other reason the user should pay attention. Be concise and avoid routine progress spam. Use form for questions requiring answers. This does not schedule a future reminder: use a trigger to call this tool at the intended time. Urgent notifications can ring, subject to user settings.",
            parameters_schema=Alert.model_json_schema(),
        )
    ],
)
