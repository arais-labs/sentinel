from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.middleware.audit import log_audit
from app.models import Trigger, TriggerLog

router = APIRouter()


@router.post("/{trigger_id}")
async def ingest_webhook(
    trigger_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    result = await db.execute(select(Trigger).where(Trigger.id == trigger_id))
    trigger = result.scalars().first()
    if trigger is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trigger not found")
    if trigger.type != "webhook":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Trigger is not a webhook trigger")
    if not trigger.enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Trigger is disabled")

    secret = (trigger.config or {}).get("secret")
    if not secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Webhook secret not configured")

    signature = request.headers.get("X-Webhook-Signature")
    if not signature:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing webhook signature")

    body = await request.body()
    expected_signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature")

    now = datetime.now(UTC)
    trigger.last_fired_at = now
    trigger.fire_count += 1

    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except json.JSONDecodeError:
        payload = {"raw": body.decode("utf-8", errors="replace")}

    log = TriggerLog(trigger_id=trigger.id, fired_at=now, status="fired", input_payload=payload)
    db.add(log)
    await db.commit()

    await log_audit(
        db,
        user_id=None,
        action="trigger.fire",
        resource_type="trigger",
        resource_id=str(trigger.id),
        status_code=200,
        ip_address=request.client.host if request.client else None,
        request_id=getattr(request.state, "request_id", None),
    )
    return {"status": "accepted"}
