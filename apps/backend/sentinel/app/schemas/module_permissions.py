"""Module action permission API schemas."""

from pydantic import BaseModel, Field


class PermissionOut(BaseModel):
    action: str
    level: str


class PermissionUpdate(BaseModel):
    level: str = Field(..., description="Permission level: allow, approval, or deny")


class PermissionListResponse(BaseModel):
    permissions: list[PermissionOut]
