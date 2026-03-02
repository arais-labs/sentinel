from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class GitAccount(Base):
    __tablename__ = "git_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    host: Mapped[str] = mapped_column(String(255), index=True)
    scope_pattern: Mapped[str] = mapped_column(String(500), server_default=text("'*'"))
    author_name: Mapped[str] = mapped_column(String(255))
    author_email: Mapped[str] = mapped_column(String(320))
    token_read: Mapped[str] = mapped_column(Text)
    token_write: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class GitPushApproval(Base):
    __tablename__ = "git_push_approvals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("git_accounts.id", ondelete="CASCADE"), index=True
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    repo_url: Mapped[str] = mapped_column(Text)
    remote_name: Mapped[str] = mapped_column(String(120), server_default=text("'origin'"))
    command: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), index=True, server_default=text("'pending'"))
    requested_by: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    decision_by: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
