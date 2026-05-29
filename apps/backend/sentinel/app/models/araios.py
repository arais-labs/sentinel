"""Dynamic module/control-plane models.

Table names are stable for existing module data.
Class names prefixed with 'Araios' to avoid collision with Sentinel models.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.services.secrets import EncryptedText


def araios_gen_id() -> str:
    return uuid.uuid4().hex


# ── Permissions ──
class AraiosPermission(Base):
    __tablename__ = "permissions"

    action: Mapped[str] = mapped_column(String, primary_key=True)
    level: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'deny'"))


# ── Module Engine ──
class AraiosModule(Base):
    __tablename__ = "modules"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    label: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, server_default=text("''"))
    icon: Mapped[str | None] = mapped_column(String, server_default=text("'box'"))
    fields: Mapped[list | None] = mapped_column(JSON, nullable=True)
    fields_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    actions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    secrets: Mapped[list | None] = mapped_column(JSON, nullable=True)
    page_title: Mapped[str | None] = mapped_column(String, nullable=True)
    page_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    system: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    order: Mapped[int] = mapped_column(Integer, server_default=text("100"))
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AraiosModuleRecord(Base):
    __tablename__ = "module_records"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=araios_gen_id)
    module_name: Mapped[str] = mapped_column(String, ForeignKey("modules.name"), nullable=False)
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AraiosModuleSecret(Base):
    __tablename__ = "module_secrets"

    module_name: Mapped[str] = mapped_column(String, ForeignKey("modules.name"), primary_key=True)
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(EncryptedText, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
