from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.column_types import UTCDateTime


class Workspace(Base):
    __tablename__ = "workspaces"
    # Remote names belong to the worker. Historical client references can retain
    # a name that the worker has since reused; UUID is the cache identity.

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120))
    machine_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    directory: Mapped[str] = mapped_column(Text)
    distribution: Mapped[str] = mapped_column(String(32), default="alpine", server_default="alpine")
    development_tools: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())


class SessionRuntimeCleanup(Base):
    """Durable outbox, intentionally independent of the deleted session row."""

    __tablename__ = "session_runtime_cleanup"

    session_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    directory: Mapped[str] = mapped_column(Text)
    tools: Mapped[list[str]] = mapped_column(JSON)
