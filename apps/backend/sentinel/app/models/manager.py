from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, Integer, String, Text, func, text
from sqlalchemy import Uuid as SQLUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.models.column_types import UTCDateTime
from app.services.secrets import EncryptedText


class ManagerBase(DeclarativeBase):
    """Declarative base for manager database tables."""


class ManagerSetting(ManagerBase):
    __tablename__ = "manager_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now()
    )


class SentinelInstance(ManagerBase):
    __tablename__ = "instances"

    # Surrogate PK so `name` can be renamed safely; FKs should reference `id`.
    id: Mapped[UUID] = mapped_column(SQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    database_name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    appearance: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now()
    )


class Machine(ManagerBase):
    __tablename__ = "machines"

    id: Mapped[UUID] = mapped_column(SQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ready")
    host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True, default=22)
    username: Mapped[str | None] = mapped_column(String(120), nullable=True)
    auth_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    encrypted_secret: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    profile: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_job_id: Mapped[UUID | None] = mapped_column(SQLUUID(as_uuid=True), nullable=True)
    last_job_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider_config: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        server_default=text("'{}'"),
        default=dict,
    )
    provider_state: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        server_default=text("'{}'"),
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now()
    )
