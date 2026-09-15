from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.column_types import UTCDateTime
from app.services.secrets import EncryptedText


class GitAccount(Base):
    __tablename__ = "git_accounts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    host: Mapped[str] = mapped_column(String(255), index=True)
    scope_pattern: Mapped[str] = mapped_column(String(500), server_default=text("'*'"))
    author_name: Mapped[str] = mapped_column(String(255))
    author_email: Mapped[str] = mapped_column(String(320))
    token: Mapped[str] = mapped_column(EncryptedText)
    github_login: Mapped[str | None] = mapped_column(String(255))
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now()
    )
