from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, ForeignKey, Index, String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.column_types import UTCDateTime

if TYPE_CHECKING:
    from app.models.sessions import Session


class SessionBinding(Base):
    __tablename__ = "session_bindings"
    __table_args__ = (
        Index(
            "ix_session_bindings_user_type_key",
            "user_id",
            "binding_type",
            "binding_key",
        ),
        Index(
            "uq_session_bindings_active_route",
            "user_id",
            "binding_type",
            "binding_key",
            unique=True,
            sqlite_where=text("is_active"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(100), index=True)
    binding_type: Mapped[str] = mapped_column(String(40), index=True)
    binding_key: Mapped[str] = mapped_column(String(255))
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), index=True
    )
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSON, nullable=False, server_default=text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    session: Mapped["Session"] = relationship()
