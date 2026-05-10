"""Pydantic schemas for dynamic module/control-plane API endpoints."""
from __future__ import annotations

from pydantic import BaseModel, Field


# ── Approvals ──


class ApprovalCreate(BaseModel):
    action: str = Field(..., description="Action identifier (e.g. 'leads.delete')")
    resource: str | None = Field(None, description="Resource type")
    resourceId: str | None = Field(None, description="Target resource ID")
    description: str | None = Field("", description="Human-readable description")
    payload: dict | None = Field(None, description="Action payload")


class ApprovalOut(BaseModel):
    id: str
    status: str
    action: str
    resource: str | None = None
    resource_id: str | None = None
    description: str | None = None
    payload: dict | None = None
    created_at: str | None = None
    resolved_at: str | None = None
    resolved_by: str | None = None


class ApprovalListResponse(BaseModel):
    approvals: list[ApprovalOut]


# ── Permissions ──


class PermissionOut(BaseModel):
    action: str
    level: str


class PermissionUpdate(BaseModel):
    level: str = Field(..., description="Permission level: allow, approval, or deny")


class PermissionListResponse(BaseModel):
    permissions: list[PermissionOut]
