from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Integer, String, Text, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.column_types import UTCDateTime

if TYPE_CHECKING:
    from app.models.sessions import Session


class SubAgentTask(Base):
    __tablename__ = "sub_agent_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_sub_agent_tasks_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    objective: Mapped[str] = mapped_column(Text)
    constraints: Mapped[list] = mapped_column(JSON, server_default=text("'[]'"))
    allowed_tools: Mapped[list] = mapped_column(JSON, server_default=text("'[]'"))
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default=text("'pending'")
    )
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    turns_used: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())

    session: Mapped["Session"] = relationship(back_populates="sub_agent_tasks")
