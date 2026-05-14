from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, JSON, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class ManagerBase(DeclarativeBase):
    """Declarative base for manager database tables."""


class ManagerSetting(ManagerBase):
    __tablename__ = "manager_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ManagerRevokedToken(ManagerBase):
    __tablename__ = "manager_revoked_tokens"

    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SentinelInstance(ManagerBase):
    __tablename__ = "instances"

    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    database_name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    workspace_root: Mapped[str] = mapped_column(Text, nullable=False)
    runtime_backend: Mapped[str] = mapped_column(String(32), nullable=False, default="docker")
    runtime_config_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
