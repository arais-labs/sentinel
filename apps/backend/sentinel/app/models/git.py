from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
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
