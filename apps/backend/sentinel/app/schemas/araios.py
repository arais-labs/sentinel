"""Pydantic schemas for AraiOS API endpoints."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ── Common ──


class OkResponse(BaseModel):
    ok: bool = True


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


# ── Coordination ──


class CoordinationSend(BaseModel):
    message: str = Field(..., description="Message content")
    context: dict | None = Field(None, description="Arbitrary metadata")


class CoordinationMessageOut(BaseModel):
    id: str
    agent: str
    message: str
    context: dict | None = None
    createdAt: str | None = None


class CoordinationListResponse(BaseModel):
    messages: list[CoordinationMessageOut]


# ── Tasks ──


class TaskCreate(BaseModel):
    client: str | None = None
    repo: str | None = None
    type: str | None = None
    priority: str | None = "medium"
    status: str | None = "todo"
    title: str | None = None
    owner: str | None = None
    createdBy: str | None = None
    updatedBy: str | None = None
    handoffTo: str | None = None
    source: str | None = None
    prUrl: str | None = None
    summary: str | None = None
    workPackage: dict | None = None
    detectedAt: str | None = None
    readyAt: str | None = None
    handedOffAt: str | None = None
    closedAt: str | None = None
    notes: str | None = None


class TaskUpdate(BaseModel):
    client: str | None = None
    repo: str | None = None
    type: str | None = None
    priority: str | None = None
    status: str | None = None
    title: str | None = None
    owner: str | None = None
    createdBy: str | None = None
    updatedBy: str | None = None
    handoffTo: str | None = None
    source: str | None = None
    prUrl: str | None = None
    summary: str | None = None
    workPackage: dict | None = None
    detectedAt: str | None = None
    readyAt: str | None = None
    handedOffAt: str | None = None
    closedAt: str | None = None
    notes: str | None = None


class TaskOut(BaseModel):
    id: str
    client: str | None = None
    repo: str | None = None
    type: str | None = None
    priority: str | None = None
    status: str | None = None
    title: str | None = None
    owner: str | None = None
    createdBy: str | None = None
    updatedBy: str | None = None
    handoffTo: str | None = None
    source: str | None = None
    prUrl: str | None = None
    summary: str | None = None
    workPackage: dict | None = None
    detectedAt: str | None = None
    readyAt: str | None = None
    handedOffAt: str | None = None
    closedAt: str | None = None
    notes: str | None = None
    updatedAt: str | None = None


class TaskListResponse(BaseModel):
    tasks: list[TaskOut]


# ── Documents ──


class DocumentCreate(BaseModel):
    slug: str = Field(..., description="URL-friendly unique identifier")
    title: str = Field(..., description="Document title")
    content: str = Field("", description="Document content (markdown)")
    tags: list[str] | None = None


class DocumentUpdate(BaseModel):
    title: str | None = None
    content: str = Field(..., description="Full document content")
    tags: list[str] | None = None


class DocumentOut(BaseModel):
    id: str
    slug: str
    title: str
    content: str
    author: str
    lastEditedBy: str
    tags: list[str] | None = None
    version: int
    createdAt: str | None = None
    updatedAt: str | None = None


class DocumentListItem(BaseModel):
    id: str
    slug: str
    title: str
    author: str
    lastEditedBy: str
    tags: list[str] | None = None
    version: int
    createdAt: str | None = None
    updatedAt: str | None = None


class DocumentListResponse(BaseModel):
    documents: list[DocumentListItem]
