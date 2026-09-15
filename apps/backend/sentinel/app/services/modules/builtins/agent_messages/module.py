from sqlalchemy import select

from app.database.database import AsyncSessionLocal
from app.models import Message
from app.services.modules.definitions import ActionDefinition, ModuleDefinition
from app.services.modules.runtime_services import get_app_state
from app.services.sub_agents.messaging import send_message
from sentral.errors import ToolValidationError
from app.services.tools.runtime_context import require_session_id


async def send(payload, runtime):
    content = payload.get("message")
    target = payload.get("target")
    if not isinstance(content, str) or not content.strip() or not isinstance(target, str):
        raise ToolValidationError("A target and non-empty message are required")
    async with AsyncSessionLocal() as db:
        return await send_message(
            db,
            sender_id=require_session_id(runtime),
            target=target,
            content=content.strip(),
            orchestrator=runtime.sub_agent_orchestrator,
            run_registry=getattr(get_app_state(), "agent_run_registry", None),
        )


async def inbox(payload, runtime):
    async with AsyncSessionLocal() as db:
        rows = (
            (
                await db.execute(
                    select(Message)
                    .where(Message.session_id == require_session_id(runtime))
                    .order_by(Message.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return {
            "messages": [
                {
                    "id": str(row.id),
                    "sender_session_id": row.metadata_json.get("sender_session_id"),
                    "content": row.content,
                    "delivery": row.metadata_json.get("steering"),
                }
                for row in rows
                if (row.metadata_json or {}).get("source") == "agent_message"
            ][:50]
        }


MODULE = ModuleDefinition(
    name="agent_messages",
    label="Agent messages",
    system=True,
    internal=True,
    grouped_tool=True,
    description="Internal communication between agents in this conversation only. Send a focused follow-up to a peer instead of routinely reading its transcript or repeating its work. Messages reach running agents at model boundaries and resume idle children. Sender identity and chat scope are enforced by the system.",
    actions=[
        ActionDefinition(
            id="send",
            label="Send",
            handler=send,
            requires_runtime_context=True,
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["target", "message"],
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Task ID, child conversation ID, or parent.",
                    },
                    "message": {"type": "string"},
                },
            },
        ),
        ActionDefinition(
            id="inbox",
            label="Inbox",
            handler=inbox,
            requires_runtime_context=True,
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {},
            },
        ),
    ],
)
