from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.middleware.auth import TokenPayload, get_agent_id, get_current_user
from app.database.models import CoordinationMessage, gen_id
from app.schemas import CoordinationSend, CoordinationMessageOut, CoordinationListResponse

router = APIRouter()


def _to_dict(obj: CoordinationMessage) -> dict:
    return {
        "id": obj.id,
        "agent": obj.agent,
        "message": obj.message,
        "context": obj.context,
        "createdAt": obj.created_at.isoformat() if obj.created_at else None,
    }


@router.post(
    "",
    status_code=201,
    response_model=CoordinationMessageOut,
    summary="Send a coordination message",
    description="Post a message to the coordination log. Agent identity is determined from the token.",
)
async def send_message(
    body: CoordinationSend,
    db: Session = Depends(get_db),
    agent_id: str = Depends(get_agent_id),
    user: TokenPayload = Depends(get_current_user),
):
    context = dict(body.context) if isinstance(body.context, dict) else {}
    if user.label and "agent_label" not in context:
        context["agent_label"] = user.label

    msg = CoordinationMessage(
        id=gen_id(),
        agent=agent_id,
        message=body.message,
        context=context or None,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return _to_dict(msg)


@router.get(
    "",
    response_model=CoordinationListResponse,
    summary="Get coordination log",
    description="Returns the most recent coordination messages, oldest first.",
)
async def get_messages(
    limit: int = Query(100, ge=1, le=500, description="Max messages to return"),
    db: Session = Depends(get_db),
    agent_id: str = Depends(get_agent_id),
):
    rows = (
        db.query(CoordinationMessage)
        .order_by(CoordinationMessage.seq.desc())
        .limit(limit)
        .all()
    )
    rows.reverse()  # Return oldest first (chat order)
    return {"messages": [_to_dict(r) for r in rows]}
