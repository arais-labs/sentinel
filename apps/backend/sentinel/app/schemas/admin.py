from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AuditLogResponse(BaseModel):
    id: UUID
    timestamp: datetime
    user_id: str | None = None
    action: str
    resource_type: str | None = None
    resource_id: str | None = None
    status_code: int | None = None
    ip_address: str | None = None
    request_id: UUID | None = None


class AuditLogListResponse(BaseModel):
    items: list[AuditLogResponse]
    total: int


class ConfigResponse(BaseModel):
    app_name: str
    app_env: str
    estop_active: bool = False
    jwt_algorithm: str
    access_token_ttl_seconds: int
    refresh_token_ttl_seconds: int
    araios_url: str | None = None
    jwt_secret_key: str = "***"
    dev_token: str = "***"


class UpdateConfigRequest(BaseModel):
    access_token_ttl_seconds: int | None = Field(default=None, ge=1)
    refresh_token_ttl_seconds: int | None = Field(default=None, ge=1)
