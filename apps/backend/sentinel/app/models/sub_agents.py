from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class SubAgentTask(Base):
    __tablename__ = "sub_agent_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_sub_agent_tasks_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    objective: Mapped[str] = mapped_column(Text)
    constraints: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    allowed_tools: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    max_turns: Mapped[int] = mapped_column(Integer, default=10)
    max_tokens: Mapped[int] = mapped_column(Integer, default=50_000)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=300)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    turns_used: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped["Session"] = relationship(back_populates="sub_agent_tasks")
