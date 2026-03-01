"""Pydantic schemas for all araiOS API endpoints."""

from typing import Optional, List, Any
from pydantic import BaseModel, Field


# ── Common ──

class OkResponse(BaseModel):
    ok: bool = True


# ── Leads ──

class LeadCreate(BaseModel):
    """Create a new lead."""
    name: str = Field(..., description="Contact name")
    role: Optional[str] = Field(None, description="Job title / role")
    company: Optional[str] = Field(None, description="Company name")
    linkedinUrl: Optional[str] = Field(None, description="LinkedIn profile URL")
    status: Optional[str] = Field("draft", description="Lead status: draft, researching, approved, sent, replied, meeting, closed")
    lastContact: Optional[str] = Field(None, description="Date of last contact (ISO string)")
    nextAction: Optional[str] = Field(None, description="Next action to take")
    notes: Optional[str] = Field(None, description="Free-form notes")
    messageDraft: Optional[str] = Field(None, description="Draft outreach message")
    approvedMessage: Optional[str] = Field(None, description="Admin-approved message ready to send")

class LeadUpdate(BaseModel):
    """Update an existing lead. All fields optional — only provided fields are updated."""
    name: Optional[str] = Field(None, description="Contact name")
    role: Optional[str] = Field(None, description="Job title / role")
    company: Optional[str] = Field(None, description="Company name")
    linkedinUrl: Optional[str] = Field(None, description="LinkedIn profile URL")
    status: Optional[str] = Field(None, description="Lead status: draft, researching, approved, sent, replied, meeting, closed")
    lastContact: Optional[str] = Field(None, description="Date of last contact (ISO string)")
    nextAction: Optional[str] = Field(None, description="Next action to take")
    notes: Optional[str] = Field(None, description="Free-form notes")
    messageDraft: Optional[str] = Field(None, description="Draft outreach message")
    approvedMessage: Optional[str] = Field(None, description="Admin-approved message ready to send")

class LeadOut(BaseModel):
    id: str
    name: Optional[str] = None
    role: Optional[str] = None
    company: Optional[str] = None
    linkedinUrl: Optional[str] = None
    status: Optional[str] = None
    lastContact: Optional[str] = None
    nextAction: Optional[str] = None
    notes: Optional[str] = None
    messageDraft: Optional[str] = None
    approvedMessage: Optional[str] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None

class LeadListResponse(BaseModel):
    leads: List[LeadOut]


# ── Competitors ──

class CompetitorCreate(BaseModel):
    """Create a new competitor entry."""
    name: str = Field(..., description="Competitor name")
    website: Optional[str] = Field(None, description="Competitor website URL")
    category: Optional[str] = Field(None, description="Market category")
    pricing: Optional[dict] = Field(None, description="Pricing info as key-value pairs (e.g. {\"starter\": \"$29/mo\", \"pro\": \"$99/mo\"})")
    strengths: Optional[List[str]] = Field(None, description="List of competitor strengths")
    weaknesses: Optional[List[str]] = Field(None, description="List of competitor weaknesses")
    notes: Optional[str] = Field(None, description="Free-form notes")

class CompetitorUpdate(BaseModel):
    """Update a competitor. All fields optional."""
    name: Optional[str] = Field(None, description="Competitor name")
    website: Optional[str] = Field(None, description="Competitor website URL")
    category: Optional[str] = Field(None, description="Market category")
    pricing: Optional[dict] = Field(None, description="Pricing info (deep-merged with existing)")
    strengths: Optional[List[str]] = Field(None, description="List of competitor strengths")
    weaknesses: Optional[List[str]] = Field(None, description="List of competitor weaknesses")
    notes: Optional[str] = Field(None, description="Free-form notes")

class CompetitorOut(BaseModel):
    id: str
    name: Optional[str] = None
    website: Optional[str] = None
    category: Optional[str] = None
    pricing: Optional[dict] = None
    strengths: Optional[List[str]] = None
    weaknesses: Optional[List[str]] = None
    notes: Optional[str] = None
    lastUpdated: Optional[str] = None

class CompetitorListResponse(BaseModel):
    competitors: List[CompetitorOut]


# ── Clients ──

class ClientCreate(BaseModel):
    """Create a new client."""
    name: str = Field(..., description="Client contact name")
    company: Optional[str] = Field(None, description="Company name")
    email: Optional[str] = Field(None, description="Email address")
    linkedIn: Optional[str] = Field(None, description="LinkedIn profile URL")
    engagementType: Optional[str] = Field(None, description="Type of engagement (e.g. consulting, retainer, project)")
    phase: Optional[str] = Field(None, description="Current project phase")
    phaseProgress: Optional[int] = Field(0, ge=0, le=100, description="Phase progress percentage (0-100)")
    healthStatus: Optional[str] = Field("green", description="Account health: green, yellow, red")
    contractValue: Optional[str] = Field(None, description="Contract value (e.g. '$5,000/mo')")
    startDate: Optional[str] = Field(None, description="Engagement start date (ISO string)")
    notes: Optional[str] = Field(None, description="Free-form notes")

class ClientUpdate(BaseModel):
    """Update a client. All fields optional."""
    name: Optional[str] = Field(None, description="Client contact name")
    company: Optional[str] = Field(None, description="Company name")
    email: Optional[str] = Field(None, description="Email address")
    linkedIn: Optional[str] = Field(None, description="LinkedIn profile URL")
    engagementType: Optional[str] = Field(None, description="Type of engagement")
    phase: Optional[str] = Field(None, description="Current project phase")
    phaseProgress: Optional[int] = Field(None, ge=0, le=100, description="Phase progress percentage (0-100)")
    healthStatus: Optional[str] = Field(None, description="Account health: green, yellow, red")
    contractValue: Optional[str] = Field(None, description="Contract value")
    startDate: Optional[str] = Field(None, description="Engagement start date (ISO string)")
    notes: Optional[str] = Field(None, description="Free-form notes")

class ClientOut(BaseModel):
    id: str
    name: Optional[str] = None
    company: Optional[str] = None
    email: Optional[str] = None
    linkedIn: Optional[str] = None
    engagementType: Optional[str] = None
    phase: Optional[str] = None
    phaseProgress: Optional[int] = None
    healthStatus: Optional[str] = None
    contractValue: Optional[str] = None
    startDate: Optional[str] = None
    notes: Optional[str] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None

class ClientListResponse(BaseModel):
    clients: List[ClientOut]


# ── Proposals ──

class ProposalCreate(BaseModel):
    """Create a new proposal. Requires admin approval for agent role."""
    leadName: Optional[str] = Field(None, description="Associated lead name")
    company: Optional[str] = Field(None, description="Company name")
    proposalTitle: Optional[str] = Field(None, description="Proposal title")
    value: Optional[int] = Field(None, description="Proposal value in dollars")
    status: Optional[str] = Field("draft", description="Proposal status: draft, sent, accepted, rejected")
    services: Optional[List[str]] = Field(None, description="List of services included")
    notes: Optional[str] = Field(None, description="Free-form notes")
    sentAt: Optional[str] = Field(None, description="Date proposal was sent (ISO string)")

class ProposalUpdate(BaseModel):
    """Update a proposal. All fields optional. Requires admin approval for agent role."""
    leadName: Optional[str] = Field(None, description="Associated lead name")
    company: Optional[str] = Field(None, description="Company name")
    proposalTitle: Optional[str] = Field(None, description="Proposal title")
    value: Optional[int] = Field(None, description="Proposal value in dollars")
    status: Optional[str] = Field(None, description="Proposal status: draft, sent, accepted, rejected")
    services: Optional[List[str]] = Field(None, description="List of services included")
    notes: Optional[str] = Field(None, description="Free-form notes")
    sentAt: Optional[str] = Field(None, description="Date proposal was sent (ISO string)")

class ProposalOut(BaseModel):
    id: str
    leadName: Optional[str] = None
    company: Optional[str] = None
    proposalTitle: Optional[str] = None
    value: Optional[int] = None
    status: Optional[str] = None
    services: Optional[List[str]] = None
    notes: Optional[str] = None
    sentAt: Optional[str] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None

class ProposalListResponse(BaseModel):
    proposals: List[ProposalOut]


# ── GitHub Tasks ──

class GithubTaskCreate(BaseModel):
    """Create a new GitHub task."""
    client: Optional[str] = Field(None, description="Client name this task belongs to")
    repo: Optional[str] = Field(None, description="GitHub repository (e.g. 'org/repo')")
    type: Optional[str] = Field(None, description="Task type: issue, pr, bug, feature")
    status: Optional[str] = Field("open", description="Task status: open, in_progress, review, handed_off, closed")
    title: Optional[str] = Field(None, description="Task title")
    source: Optional[str] = Field(None, description="Where the task originated (e.g. 'github', 'manual')")
    prUrl: Optional[str] = Field(None, description="Pull request URL")
    summary: Optional[str] = Field(None, description="Task summary / description")
    workPackage: Optional[dict] = Field(None, description="Work package details as key-value pairs")
    detectedAt: Optional[str] = Field(None, description="When the task was detected (ISO string)")
    readyAt: Optional[str] = Field(None, description="When the task became ready (ISO string)")
    handedOffAt: Optional[str] = Field(None, description="When the task was handed off (ISO string)")
    closedAt: Optional[str] = Field(None, description="When the task was closed (ISO string)")
    notes: Optional[str] = Field(None, description="Free-form notes")

class GithubTaskUpdate(BaseModel):
    """Update a GitHub task. All fields optional."""
    client: Optional[str] = Field(None, description="Client name")
    repo: Optional[str] = Field(None, description="GitHub repository")
    type: Optional[str] = Field(None, description="Task type")
    status: Optional[str] = Field(None, description="Task status: open, in_progress, review, handed_off, closed")
    title: Optional[str] = Field(None, description="Task title")
    source: Optional[str] = Field(None, description="Task origin")
    prUrl: Optional[str] = Field(None, description="Pull request URL")
    summary: Optional[str] = Field(None, description="Task summary")
    workPackage: Optional[dict] = Field(None, description="Work package details (deep-merged with existing)")
    detectedAt: Optional[str] = Field(None, description="When the task was detected")
    readyAt: Optional[str] = Field(None, description="When the task became ready")
    handedOffAt: Optional[str] = Field(None, description="When the task was handed off")
    closedAt: Optional[str] = Field(None, description="When the task was closed")
    notes: Optional[str] = Field(None, description="Free-form notes")

class GithubTaskOut(BaseModel):
    id: str
    client: Optional[str] = None
    repo: Optional[str] = None
    type: Optional[str] = None
    status: Optional[str] = None
    title: Optional[str] = None
    source: Optional[str] = None
    prUrl: Optional[str] = None
    summary: Optional[str] = None
    workPackage: Optional[dict] = None
    detectedAt: Optional[str] = None
    readyAt: Optional[str] = None
    handedOffAt: Optional[str] = None
    closedAt: Optional[str] = None
    notes: Optional[str] = None
    updatedAt: Optional[str] = None

class GithubTaskListResponse(BaseModel):
    tasks: List[GithubTaskOut]


# ── Launch Prep ──

class LaunchPrepCreate(BaseModel):
    """Create a new launch prep task."""
    title: str = Field(..., description="Task title")
    description: Optional[str] = Field(None, description="Task description")
    priority: Optional[str] = Field("medium", description="Priority: low, medium, high, critical")
    status: Optional[str] = Field("todo", description="Status: todo, in_progress, done, blocked")
    category: Optional[str] = Field(None, description="Task category")
    effort: Optional[str] = Field(None, description="Effort estimate (e.g. 'small', 'medium', 'large')")
    notes: Optional[str] = Field(None, description="Free-form notes")

class LaunchPrepUpdate(BaseModel):
    """Update a launch prep task. All fields optional."""
    title: Optional[str] = Field(None, description="Task title")
    description: Optional[str] = Field(None, description="Task description")
    priority: Optional[str] = Field(None, description="Priority: low, medium, high, critical")
    status: Optional[str] = Field(None, description="Status: todo, in_progress, done, blocked")
    category: Optional[str] = Field(None, description="Task category")
    effort: Optional[str] = Field(None, description="Effort estimate (e.g. 'small', 'medium', 'large')")
    notes: Optional[str] = Field(None, description="Free-form notes")

class LaunchPrepOut(BaseModel):
    id: str
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    category: Optional[str] = None
    effort: Optional[str] = None
    notes: Optional[str] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None

class LaunchPrepListResponse(BaseModel):
    tasks: List[LaunchPrepOut]


# ── Positioning ──

class PositioningUpdate(BaseModel):
    """Update the positioning document. All fields optional."""
    tagline: Optional[str] = Field(None, description="Company tagline")
    valueProps: Optional[List[str]] = Field(None, description="List of value propositions")
    icp: Optional[dict] = Field(None, description="Ideal Customer Profile as key-value pairs (deep-merged with existing)")
    differentiators: Optional[List[str]] = Field(None, description="List of differentiators")
    competitors: Optional[str] = Field(None, description="Competitor landscape summary")
    positioning: Optional[str] = Field(None, description="Positioning statement (long-form text)")
    objections: Optional[List[str]] = Field(None, description="List of common objections")
    notes: Optional[str] = Field(None, description="Free-form notes")

class PositioningOut(BaseModel):
    tagline: Optional[str] = None
    valueProps: Optional[List[str]] = None
    icp: Optional[dict] = None
    differentiators: Optional[List[str]] = None
    competitors: Optional[str] = None
    positioning: Optional[str] = None
    objections: Optional[List[str]] = None
    notes: Optional[str] = None


# ── Security Audit ──

class SecurityFindingCreate(BaseModel):
    """Create a new security finding."""
    title: str = Field(..., description="Finding title")
    description: Optional[str] = Field(None, description="Finding description")
    severity: str = Field(..., description="Severity: info, low, medium, high, critical")
    status: Optional[str] = Field("open", description="Status: open, in_progress, mitigated, accepted")
    category: Optional[str] = Field(None, description="Category (e.g. 'auth', 'injection', 'config')")
    fixNotes: Optional[str] = Field(None, description="Notes on how the finding was fixed")

class SecurityFindingUpdate(BaseModel):
    """Update a security finding. All fields optional."""
    title: Optional[str] = Field(None, description="Finding title")
    description: Optional[str] = Field(None, description="Finding description")
    severity: Optional[str] = Field(None, description="Severity: info, low, medium, high, critical")
    status: Optional[str] = Field(None, description="Status: open, in_progress, mitigated, accepted")
    category: Optional[str] = Field(None, description="Category (e.g. 'auth', 'injection', 'config')")
    fixNotes: Optional[str] = Field(None, description="Notes on how the finding was fixed")

class SecurityFindingOut(BaseModel):
    id: str
    title: Optional[str] = None
    description: Optional[str] = None
    severity: Optional[str] = None
    status: Optional[str] = None
    category: Optional[str] = None
    fixNotes: Optional[str] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None

class SecurityFindingListResponse(BaseModel):
    findings: List[SecurityFindingOut]


# ── Approvals ──

class ApprovalCreate(BaseModel):
    """Manually create an approval request."""
    action: str = Field(..., description="Action identifier (e.g. 'slack.send', 'leads.delete')")
    resource: Optional[str] = Field(None, description="Resource type (e.g. 'leads', 'slack')")
    resourceId: Optional[str] = Field(None, description="Target resource ID")
    description: Optional[str] = Field("", description="Human-readable description of what this approval is for")
    payload: Optional[dict] = Field(None, description="Action payload — contents depend on the action type")

class ApprovalOut(BaseModel):
    id: str
    status: str = Field(..., description="Approval status: pending, approved, rejected")
    action: str = Field(..., description="Action identifier")
    resource: Optional[str] = None
    resource_id: Optional[str] = None
    description: Optional[str] = None
    payload: Optional[dict] = None
    created_at: Optional[str] = None
    resolved_at: Optional[str] = None
    resolved_by: Optional[str] = None

class ApprovalListResponse(BaseModel):
    approvals: List[ApprovalOut]


# ── Permissions ──

class PermissionOut(BaseModel):
    action: str
    level: str

class PermissionUpdate(BaseModel):
    level: str = Field(..., description="Permission level: allow, approval, or deny")

class PermissionListResponse(BaseModel):
    permissions: List[PermissionOut]


# ── Coordination ──

class CoordinationSend(BaseModel):
    message: str = Field(..., description="Message content")
    context: Optional[dict] = Field(None, description="Arbitrary metadata (telegram_msg_id, etc.)")

class CoordinationMessageOut(BaseModel):
    id: str
    agent: str
    message: str
    context: Optional[dict] = None
    createdAt: Optional[str] = None

class CoordinationListResponse(BaseModel):
    messages: List[CoordinationMessageOut]


# ── Documents ──

class DocumentCreate(BaseModel):
    slug: str = Field(..., description="URL-friendly unique identifier")
    title: str = Field(..., description="Document title")
    content: str = Field("", description="Document content (markdown)")
    tags: Optional[List[str]] = Field(None, description="List of tags")

class DocumentUpdate(BaseModel):
    title: Optional[str] = Field(None, description="Document title")
    content: str = Field(..., description="Full document content (replaces existing)")
    tags: Optional[List[str]] = Field(None, description="List of tags")

class DocumentOut(BaseModel):
    id: str
    slug: str
    title: str
    content: str
    author: str
    lastEditedBy: str
    tags: Optional[List[str]] = None
    version: int
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None

class DocumentListItem(BaseModel):
    id: str
    slug: str
    title: str
    author: str
    lastEditedBy: str
    tags: Optional[List[str]] = None
    version: int
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None

class DocumentListResponse(BaseModel):
    documents: List[DocumentListItem]
