"""AraiOS models — ported to async SQLAlchemy (Mapped/mapped_column).

Table names preserved from original AraiOS to avoid data migration.
Class names prefixed with 'Araios' to avoid collision with Sentinel models.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def araios_gen_id() -> str:
    return uuid.uuid4().hex[:8]


# ── Leads ──


class AraiosLead(Base):
    __tablename__ = "leads"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=araios_gen_id)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    role: Mapped[str | None] = mapped_column(String, nullable=True)
    company: Mapped[str | None] = mapped_column(String, nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str | None] = mapped_column(String, server_default=text("'draft'"))
    last_contact: Mapped[str | None] = mapped_column(String, nullable=True)
    next_action: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_draft: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ── Competitors ──


class AraiosCompetitor(Base):
    __tablename__ = "competitors"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=araios_gen_id)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    website: Mapped[str | None] = mapped_column(String, nullable=True)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    pricing: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    strengths: Mapped[list | None] = mapped_column(JSON, nullable=True)
    weaknesses: Mapped[list | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── Clients ──


class AraiosClient(Base):
    __tablename__ = "clients"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=araios_gen_id)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    company: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    linked_in: Mapped[str | None] = mapped_column(String, nullable=True)
    engagement_type: Mapped[str | None] = mapped_column(String, nullable=True)
    phase: Mapped[str | None] = mapped_column(String, nullable=True)
    phase_progress: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    health_status: Mapped[str | None] = mapped_column(String, server_default=text("'green'"))
    contract_value: Mapped[str | None] = mapped_column(String, nullable=True)
    start_date: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ── Proposals ──


class AraiosProposal(Base):
    __tablename__ = "proposals"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=araios_gen_id)
    lead_name: Mapped[str | None] = mapped_column(String, nullable=True)
    company: Mapped[str | None] = mapped_column(String, nullable=True)
    proposal_title: Mapped[str | None] = mapped_column(String, nullable=True)
    value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str | None] = mapped_column(String, server_default=text("'draft'"))
    services: Mapped[list | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ── Tasks ──


class AraiosTask(Base):
    __tablename__ = "github_tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=araios_gen_id)
    client: Mapped[str | None] = mapped_column(String, nullable=True)
    repo: Mapped[str | None] = mapped_column(String, nullable=True)
    type: Mapped[str | None] = mapped_column(String, nullable=True)
    priority: Mapped[str | None] = mapped_column(String, server_default=text("'medium'"))
    status: Mapped[str | None] = mapped_column(String, server_default=text("'open'"))
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    owner: Mapped[str | None] = mapped_column(String, nullable=True)
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String, nullable=True)
    handoff_to: Mapped[str | None] = mapped_column(String, nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    work_package: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    detected_at: Mapped[str | None] = mapped_column(String, nullable=True)
    ready_at: Mapped[str | None] = mapped_column(String, nullable=True)
    handed_off_at: Mapped[str | None] = mapped_column(String, nullable=True)
    closed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ── Launch Prep ──


class AraiosLaunchPrepTask(Base):
    __tablename__ = "launch_prep"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=araios_gen_id)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[str | None] = mapped_column(String, server_default=text("'medium'"))
    status: Mapped[str | None] = mapped_column(String, server_default=text("'todo'"))
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    effort: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ── Positioning ──


class AraiosPositioning(Base):
    __tablename__ = "positioning"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: "default")
    tagline: Mapped[str | None] = mapped_column(String, nullable=True)
    value_props: Mapped[list | None] = mapped_column(JSON, nullable=True)
    icp: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    differentiators: Mapped[list | None] = mapped_column(JSON, nullable=True)
    competitors: Mapped[str | None] = mapped_column(String, nullable=True)
    positioning: Mapped[str | None] = mapped_column(Text, nullable=True)
    objections: Mapped[list | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


# ── Security Audit ──


class AraiosSecurityFinding(Base):
    __tablename__ = "security_audit"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=araios_gen_id)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str | None] = mapped_column(String, server_default=text("'open'"))
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    fix_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ── Approvals ──


class AraiosApproval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=araios_gen_id)
    status: Mapped[str] = mapped_column(String, server_default=text("'pending'"))
    action: Mapped[str] = mapped_column(String, nullable=False)
    resource: Mapped[str | None] = mapped_column(String, nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String, nullable=True)


# ── Permissions ──


class AraiosPermission(Base):
    __tablename__ = "permissions"

    action: Mapped[str] = mapped_column(String, primary_key=True)
    level: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'deny'"))


# ── Coordination ──


class AraiosCoordinationMessage(Base):
    __tablename__ = "coordination_log"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String, unique=True, default=araios_gen_id)
    agent: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
    pinned: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    system: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    order: Mapped[int] = mapped_column(Integer, server_default=text("100"))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AraiosModuleRecord(Base):
    __tablename__ = "module_records"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=araios_gen_id)
    module_name: Mapped[str] = mapped_column(String, ForeignKey("modules.name"), nullable=False)
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AraiosModuleSecret(Base):
    __tablename__ = "module_secrets"

    module_name: Mapped[str] = mapped_column(String, ForeignKey("modules.name"), primary_key=True)
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ── Documents ──


class AraiosDocument(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=araios_gen_id)
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    author: Mapped[str] = mapped_column(String, nullable=False)
    last_edited_by: Mapped[str] = mapped_column(String, nullable=False)
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ── Platform API Keys ──


class AraiOSPlatformApiKey(Base):
    __tablename__ = "platform_api_keys"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=araios_gen_id)
    label: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'agent'"))
    subject: Mapped[str] = mapped_column(String, nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String, nullable=True)
    key_hash: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
