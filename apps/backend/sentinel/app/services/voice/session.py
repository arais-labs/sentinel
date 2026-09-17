"""The instance's single Voice conversation: a hidden session of kind VOICE."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Session, SessionKind

VOICE_TITLE = "Voice"


async def find_voice_session(db: AsyncSession) -> Session | None:
    result = await db.execute(
        select(Session).where(Session.kind == SessionKind.VOICE).order_by(Session.created_at.desc())
    )
    return result.scalars().first()


async def voice_session(db: AsyncSession) -> Session:
    """Return the Voice session, creating it on first use. Never listed as a chat."""
    session = await find_voice_session(db)
    if session is None:
        session = Session(
            user_id="local", title=VOICE_TITLE, status="active", kind=SessionKind.VOICE
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
    return session


async def reset_voice_session(db: AsyncSession) -> Session:
    """Discard the Voice conversation (messages, summaries, approvals cascade) and start fresh.

    The attached workspace is a Voice setting, not conversation state: it carries over.
    """
    existing = await find_voice_session(db)
    workspace_id = existing.workspace_id if existing is not None else None
    if existing is not None:
        await db.delete(existing)
        await db.commit()
    session = await voice_session(db)
    if workspace_id and session.workspace_id != workspace_id:
        session.workspace_id = workspace_id
        await db.commit()
        await db.refresh(session)
    return session
