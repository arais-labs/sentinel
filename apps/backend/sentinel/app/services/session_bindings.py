from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Session, SessionBinding

MAIN_BINDING_TYPE = "main"
MAIN_BINDING_KEY = "owner"
TELEGRAM_GROUP_BINDING_TYPE = "telegram_group"
TELEGRAM_DM_BINDING_TYPE = "telegram_dm"


class SessionBindingError(Exception):
    """Base error for session binding operations."""


class SessionBindingTargetInvalidError(SessionBindingError):
    """Raised when a binding target session is invalid for the caller/user."""


async def get_active_binding(
    db: AsyncSession,
    *,
    user_id: str,
    binding_type: str,
    binding_key: str,
) -> SessionBinding | None:
    result = await db.execute(
        select(SessionBinding).where(
            SessionBinding.user_id == user_id,
            SessionBinding.binding_type == binding_type,
            SessionBinding.binding_key == binding_key,
            SessionBinding.is_active.is_(True),
        )
    )
    return result.scalars().first()


async def get_active_binding_session(
    db: AsyncSession,
    *,
    user_id: str,
    binding_type: str,
    binding_key: str,
) -> Session | None:
    result = await db.execute(
        select(Session)
        .join(SessionBinding, SessionBinding.session_id == Session.id)
        .where(
            SessionBinding.user_id == user_id,
            SessionBinding.binding_type == binding_type,
            SessionBinding.binding_key == binding_key,
            SessionBinding.is_active.is_(True),
            Session.user_id == user_id,
            Session.parent_session_id.is_(None),
        )
    )
    return result.scalars().first()


async def bind_session(
    db: AsyncSession,
    *,
    user_id: str,
    binding_type: str,
    binding_key: str,
    session_id: UUID,
    metadata: dict[str, Any] | None = None,
) -> SessionBinding:
    session = await _get_root_owned_session(db, user_id=user_id, session_id=session_id)
    if session is None:
        raise SessionBindingTargetInvalidError("Binding target must be a root session owned by user")

    if binding_type == MAIN_BINDING_TYPE:
        await _deactivate_active_bindings(
            db,
            user_id=user_id,
            binding_type=MAIN_BINDING_TYPE,
            keep_key=binding_key,
        )

    existing_active = await get_active_binding(
        db,
        user_id=user_id,
        binding_type=binding_type,
        binding_key=binding_key,
    )
    if existing_active is not None and existing_active.session_id != session.id:
        existing_active.is_active = False

    reusable_result = await db.execute(
        select(SessionBinding)
        .where(
            SessionBinding.user_id == user_id,
            SessionBinding.binding_type == binding_type,
            SessionBinding.binding_key == binding_key,
        )
        .order_by(SessionBinding.updated_at.desc(), SessionBinding.created_at.desc())
    )
    reusable = reusable_result.scalars().first()
    payload = metadata or {}
    if reusable is None:
        reusable = SessionBinding(
            user_id=user_id,
            binding_type=binding_type,
            binding_key=binding_key,
            session_id=session.id,
            is_active=True,
            metadata_json=payload,
        )
        db.add(reusable)
    else:
        reusable.session_id = session.id
        reusable.is_active = True
        reusable.metadata_json = payload

    await db.flush()
    return reusable


async def resolve_main_session_id(
    db: AsyncSession,
    *,
    user_id: str,
) -> UUID | None:
    session = await get_active_binding_session(
        db,
        user_id=user_id,
        binding_type=MAIN_BINDING_TYPE,
        binding_key=MAIN_BINDING_KEY,
    )
    return session.id if session is not None else None


async def resolve_or_create_main_session(
    db: AsyncSession,
    *,
    user_id: str,
    agent_id: str | None,
) -> Session:
    bound_main = await get_active_binding_session(
        db,
        user_id=user_id,
        binding_type=MAIN_BINDING_TYPE,
        binding_key=MAIN_BINDING_KEY,
    )
    if bound_main is not None:
        return bound_main

    roots = await _root_sessions(db, user_id=user_id)
    if roots:
        roots.sort(key=lambda item: item.created_at or _utc_min())
        selected = roots[0]
        await bind_session(
            db,
            user_id=user_id,
            binding_type=MAIN_BINDING_TYPE,
            binding_key=MAIN_BINDING_KEY,
            session_id=selected.id,
            metadata={"source": "resolved_root"},
        )
        return selected

    now = datetime.now(UTC)
    created = Session(
        user_id=user_id,
        agent_id=agent_id,
        title="Main",
        started_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(created)
    await db.flush()
    await bind_session(
        db,
        user_id=user_id,
        binding_type=MAIN_BINDING_TYPE,
        binding_key=MAIN_BINDING_KEY,
        session_id=created.id,
        metadata={"source": "created_main"},
    )
    return created


async def set_main_session(
    db: AsyncSession,
    *,
    user_id: str,
    session_id: UUID,
) -> Session:
    session = await _get_root_owned_session(db, user_id=user_id, session_id=session_id)
    if session is None:
        raise SessionBindingTargetInvalidError("Main session must be a root session owned by user")
    if await _is_telegram_route_session(db, user_id=user_id, session_id=session.id):
        raise SessionBindingTargetInvalidError(
            "Telegram channel sessions cannot be set as main"
        )

    await bind_session(
        db,
        user_id=user_id,
        binding_type=MAIN_BINDING_TYPE,
        binding_key=MAIN_BINDING_KEY,
        session_id=session.id,
        metadata={"source": "set_main"},
    )
    return session


async def is_session_bound(
    db: AsyncSession,
    *,
    user_id: str,
    session_id: UUID,
    binding_types: set[str] | None = None,
    active_only: bool = True,
) -> bool:
    query = select(SessionBinding).where(
        SessionBinding.user_id == user_id,
        SessionBinding.session_id == session_id,
    )
    if binding_types:
        query = query.where(SessionBinding.binding_type.in_(binding_types))
    if active_only:
        query = query.where(SessionBinding.is_active.is_(True))
    result = await db.execute(query.limit(1))
    return result.scalars().first() is not None


async def _get_root_owned_session(
    db: AsyncSession, *, user_id: str, session_id: UUID
) -> Session | None:
    result = await db.execute(
        select(Session).where(
            Session.id == session_id,
            Session.user_id == user_id,
            Session.parent_session_id.is_(None),
        )
    )
    return result.scalars().first()


async def _deactivate_active_bindings(
    db: AsyncSession,
    *,
    user_id: str,
    binding_type: str,
    keep_key: str | None = None,
) -> None:
    result = await db.execute(
        select(SessionBinding).where(
            SessionBinding.user_id == user_id,
            SessionBinding.binding_type == binding_type,
            SessionBinding.is_active.is_(True),
        )
    )
    rows = result.scalars().all()
    for row in rows:
        if keep_key is not None and row.binding_key == keep_key:
            continue
        row.is_active = False


async def _is_telegram_route_session(
    db: AsyncSession,
    *,
    user_id: str,
    session_id: UUID,
) -> bool:
    return await is_session_bound(
        db,
        user_id=user_id,
        session_id=session_id,
        binding_types={TELEGRAM_GROUP_BINDING_TYPE, TELEGRAM_DM_BINDING_TYPE},
        active_only=True,
    )


async def _root_sessions(
    db: AsyncSession,
    *,
    user_id: str,
) -> list[Session]:
    query = select(Session).where(
        Session.user_id == user_id,
        Session.parent_session_id.is_(None),
    )
    result = await db.execute(query)
    return result.scalars().all()


def _utc_min() -> datetime:
    return datetime.min.replace(tzinfo=UTC)
