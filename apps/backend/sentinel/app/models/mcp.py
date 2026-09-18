"""Instance-owned MCP connections. Connection credentials are encrypted at rest."""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Uuid, false, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base
from app.models.column_types import UTCDateTime
from app.services.secrets import EncryptedText


class MCPServer(Base):
    __tablename__ = "mcp_servers"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    config: Mapped[str] = mapped_column(EncryptedText)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Tools of a pinned server are in every prompt; others load on demand per session.
    always_load: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    tools: Mapped[list] = mapped_column(JSON, default=list)


class MCPSessionExposure(Base):
    """Which on-demand servers a chat session has loaded, and when they were last used."""

    __tablename__ = "mcp_session_exposure"
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True
    )
    server_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("mcp_servers.id", ondelete="CASCADE"), primary_key=True
    )
    # Assistant-message counts: a cheap, monotonic per-session clock.
    loaded_at: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now()
    )
