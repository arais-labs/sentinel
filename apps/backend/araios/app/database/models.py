import uuid

from sqlalchemy import Column, String, Text, DateTime, Integer, JSON, func, Boolean, ForeignKey

from app.database.database import Base


def gen_id():
    return uuid.uuid4().hex[:8]


# ── Leads ──

class Lead(Base):
    __tablename__ = "leads"

    id = Column(String, primary_key=True, default=gen_id)
    name = Column(String)
    role = Column(String)
    company = Column(String)
    linkedin_url = Column(String)
    status = Column(String, default="draft")
    last_contact = Column(String)
    next_action = Column(String)
    notes = Column(Text)
    message_draft = Column(Text)
    approved_message = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ── Competitors ──

class Competitor(Base):
    __tablename__ = "competitors"

    id = Column(String, primary_key=True, default=gen_id)
    name = Column(String)
    website = Column(String)
    category = Column(String)
    pricing = Column(JSON, default=dict)
    strengths = Column(JSON, default=list)
    weaknesses = Column(JSON, default=list)
    notes = Column(Text)
    last_updated = Column(DateTime(timezone=True), server_default=func.now())


# ── Clients ──

class Client(Base):
    __tablename__ = "clients"

    id = Column(String, primary_key=True, default=gen_id)
    name = Column(String)
    company = Column(String)
    email = Column(String)
    linked_in = Column(String)
    engagement_type = Column(String)
    phase = Column(String)
    phase_progress = Column(Integer, default=0)
    health_status = Column(String, default="green")
    contract_value = Column(String)
    start_date = Column(String)
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ── Proposals ──

class Proposal(Base):
    __tablename__ = "proposals"

    id = Column(String, primary_key=True, default=gen_id)
    lead_name = Column(String)
    company = Column(String)
    proposal_title = Column(String)
    value = Column(Integer)
    status = Column(String, default="draft")
    services = Column(JSON, default=list)
    notes = Column(Text)
    sent_at = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ── GitHub Tasks ──

class GithubTask(Base):
    __tablename__ = "github_tasks"

    id = Column(String, primary_key=True, default=gen_id)
    client = Column(String)
    repo = Column(String)
    type = Column(String)
    status = Column(String, default="open")
    title = Column(String)
    source = Column(String)
    pr_url = Column(String)
    summary = Column(Text)
    work_package = Column(JSON, default=dict)
    detected_at = Column(String)
    ready_at = Column(String)
    handed_off_at = Column(String)
    closed_at = Column(String)
    notes = Column(Text)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ── Launch Prep ──

class LaunchPrepTask(Base):
    __tablename__ = "launch_prep"

    id = Column(String, primary_key=True, default=gen_id)
    title = Column(String)
    description = Column(Text)
    priority = Column(String, default="medium")
    status = Column(String, default="todo")
    category = Column(String)
    effort = Column(String)
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ── Positioning (single-row) ──

class Positioning(Base):
    __tablename__ = "positioning"

    id = Column(String, primary_key=True, default=lambda: "default")
    tagline = Column(String)
    value_props = Column(JSON, default=list)
    icp = Column(JSON, default=dict)
    differentiators = Column(JSON, default=list)
    competitors = Column(String)
    positioning = Column(Text)
    objections = Column(JSON, default=list)
    notes = Column(Text)


# ── Security Audit ──

class SecurityFinding(Base):
    __tablename__ = "security_audit"

    id = Column(String, primary_key=True, default=gen_id)
    title = Column(String)
    description = Column(Text)
    severity = Column(String)
    status = Column(String, default="open")
    category = Column(String)
    fix_notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ── Approvals ──

class Approval(Base):
    __tablename__ = "approvals"

    id = Column(String, primary_key=True, default=gen_id)
    status = Column(String, default="pending")  # pending | approved | rejected
    action = Column(String, nullable=False)      # e.g. "leads.delete"
    resource = Column(String)                    # e.g. "leads"
    resource_id = Column(String)                 # target resource ID
    description = Column(Text)
    payload = Column(JSON)                       # original request body
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at = Column(DateTime(timezone=True))
    resolved_by = Column(String)


# ── Permissions ──

class Permission(Base):
    __tablename__ = "permissions"

    action = Column(String, primary_key=True)   # e.g. "leads.delete"
    level = Column(String, nullable=False, default="deny")  # allow | approval | deny


# ── Coordination ──

class CoordinationMessage(Base):
    __tablename__ = "coordination_log"

    seq = Column(Integer, primary_key=True, autoincrement=True)
    id = Column(String, unique=True, default=gen_id)
    agent = Column(String, nullable=False)      # e.g. "esprit", "ronnor"
    message = Column(Text, nullable=False)
    context = Column(JSON)                      # arbitrary metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ── Module Engine ──

class Module(Base):
    __tablename__ = "modules"

    name        = Column(String, primary_key=True)   # slug: "leads"
    label       = Column(String, nullable=False)
    description = Column(Text, default="")
    icon        = Column(String, default="box")
    type        = Column(String, default="data")     # data | page | tool
    fields      = Column(JSON, default=list)
    list_config = Column(JSON, default=dict)
    actions     = Column(JSON, default=list)
    secrets     = Column(JSON, default=list)         # [{key, env_var, label, required}]
    is_system   = Column(Boolean, default=False)     # True = can't delete
    order       = Column(Integer, default=100)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    updated_at  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ModuleRecord(Base):
    __tablename__ = "module_records"

    id          = Column(String, primary_key=True, default=gen_id)
    module_name = Column(String, ForeignKey("modules.name"), nullable=False)
    data        = Column(JSON, default=dict)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    updated_at  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ModuleSecret(Base):
    """Runtime-configurable secrets for a module (e.g. API tokens).
    Values are stored as-is; never returned to the frontend."""
    __tablename__ = "module_secrets"

    module_name = Column(String, ForeignKey("modules.name"), primary_key=True)
    key         = Column(String, primary_key=True)   # matches Module.secrets[].key
    value       = Column(String, nullable=False)
    updated_at  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ── Settings ──

class Setting(Base):
    __tablename__ = "settings"

    key        = Column(String, primary_key=True)
    value      = Column(Text, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ── Documents ──

class Document(Base):
    __tablename__ = "documents"

    id = Column(String, primary_key=True, default=gen_id)
    slug = Column(String, unique=True, nullable=False)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=False, default="")
    author = Column(String, nullable=False)
    last_edited_by = Column(String, nullable=False)
    tags = Column(JSON, default=list)
    version = Column(Integer, default=1)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ── Platform Auth ──

class PlatformApiKey(Base):
    __tablename__ = "platform_api_keys"

    id = Column(String, primary_key=True, default=gen_id)
    label = Column(String, nullable=False)
    role = Column(String, nullable=False, default="agent")
    subject = Column(String, nullable=False)
    agent_id = Column(String, nullable=True)
    key_hash = Column(String, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
