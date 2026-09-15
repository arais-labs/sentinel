from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, ForeignKey, String, Text, UniqueConstraint, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.column_types import UTCDateTime


class ToolApproval(Base):
    __tablename__ = "tool_approvals"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(40), server_default=text("'tool'"), index=True)
    tool_name: Mapped[str] = mapped_column(String(120), index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(160), index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    match_key: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), server_default=text("'pending'"), index=True)
    requested_by: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    decision_by: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now()
    )


class SessionActionGrant(Base):
    __tablename__ = "session_action_grants"
    __table_args__ = (UniqueConstraint("session_id", "action"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[str] = mapped_column(String(160))
    approved_by: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
