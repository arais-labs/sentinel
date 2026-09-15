from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.column_types import EmbeddingVector, UTCDateTime

if TYPE_CHECKING:
    from app.models.sessions import Session


class SessionSummary(Base):
    __tablename__ = "session_summaries"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    summary: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())

    session: Mapped["Session"] = relationship(back_populates="summaries")


class Memory(Base):
    __tablename__ = "memories"
    __table_args__ = (
        Index("uq_memories_system_key", "system_key", unique=True, sqlite_where=text("is_system")),
        CheckConstraint(
            "category IN ('core', 'preference', 'project', 'correction')",
            name="ck_memories_category",
        ),
        CheckConstraint("importance >= 0 AND importance <= 100", name="ck_memories_importance"),
        CheckConstraint(
            "((is_system AND system_key IS NOT NULL) OR (NOT is_system AND system_key IS NULL))",
            name="ck_memories_system_key_consistency",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(50), index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("memories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    importance: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    pinned: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    is_system: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        index=True,
    )
    system_key: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    embedding: Mapped[list[float] | None] = mapped_column(EmbeddingVector(), nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, server_default=text("'{}'"))
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True
    )
    last_accessed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now()
    )
    parent: Mapped["Memory | None"] = relationship(
        "Memory",
        remote_side="Memory.id",
        back_populates="children",
    )
    children: Mapped[list["Memory"]] = relationship(
        "Memory",
        back_populates="parent",
        cascade="save-update",
    )
