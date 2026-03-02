"""Seed the database from JSON data files.

Usage:
    cd backend && python -m app.database.seed
"""

import json
import os
import sys

# Add backend/ to path so config imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.database.database import SessionLocal, init_db
from app.database.models import (
    Lead, Competitor, Client, Proposal, Task,
    LaunchPrepTask, Positioning, SecurityFinding,
)


DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")

# camelCase → snake_case field maps per resource
_LEAD_MAP = {"linkedinUrl": "linkedin_url", "lastContact": "last_contact", "nextAction": "next_action", "messageDraft": "message_draft", "approvedMessage": "approved_message", "createdAt": "created_at", "updatedAt": "updated_at"}
_COMPETITOR_MAP = {"lastUpdated": "last_updated"}
_CLIENT_MAP = {"linkedIn": "linked_in", "engagementType": "engagement_type", "phaseProgress": "phase_progress", "healthStatus": "health_status", "contractValue": "contract_value", "startDate": "start_date", "createdAt": "created_at", "updatedAt": "updated_at"}
_PROPOSAL_MAP = {"leadName": "lead_name", "proposalTitle": "proposal_title", "sentAt": "sent_at", "createdAt": "created_at", "updatedAt": "updated_at"}
_TASK_MAP = {
    "prUrl": "pr_url",
    "workPackage": "work_package",
    "detectedAt": "detected_at",
    "readyAt": "ready_at",
    "handedOffAt": "handed_off_at",
    "closedAt": "closed_at",
    "createdBy": "created_by",
    "updatedBy": "updated_by",
    "handoffTo": "handoff_to",
    "updatedAt": "updated_at",
}
_LAUNCH_MAP = {"createdAt": "created_at", "updatedAt": "updated_at"}
_POSITION_MAP = {"valueProps": "value_props"}
_SECURITY_MAP = {"fixNotes": "fix_notes", "createdAt": "created_at", "updatedAt": "updated_at"}


def _map_fields(data: dict, field_map: dict, model_cls) -> dict:
    columns = {c.name for c in model_cls.__table__.columns}
    out = {}
    for k, v in data.items():
        col = field_map.get(k, k)
        if col in columns:
            out[col] = v
    return out


def _load_json(filename: str) -> dict:
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        print(f"  Skipping {filename} (not found)")
        return {}
    with open(path) as f:
        return json.load(f)


def seed():
    init_db()
    db = SessionLocal()

    try:
        # Leads
        data = _load_json("leads.json")
        for item in data.get("leads", []):
            if not db.query(Lead).filter(Lead.id == item["id"]).first():
                db.add(Lead(**_map_fields(item, _LEAD_MAP, Lead)))
        print(f"  Seeded {len(data.get('leads', []))} leads")

        # Competitors
        data = _load_json("competitors.json")
        for item in data.get("competitors", []):
            if not db.query(Competitor).filter(Competitor.id == item["id"]).first():
                db.add(Competitor(**_map_fields(item, _COMPETITOR_MAP, Competitor)))
        print(f"  Seeded {len(data.get('competitors', []))} competitors")

        # Clients
        data = _load_json("clients.json")
        for item in data.get("clients", []):
            if not db.query(Client).filter(Client.id == item["id"]).first():
                db.add(Client(**_map_fields(item, _CLIENT_MAP, Client)))
        print(f"  Seeded {len(data.get('clients', []))} clients")

        # Proposals
        data = _load_json("proposals.json")
        for item in data.get("proposals", []):
            if not db.query(Proposal).filter(Proposal.id == item["id"]).first():
                db.add(Proposal(**_map_fields(item, _PROPOSAL_MAP, Proposal)))
        print(f"  Seeded {len(data.get('proposals', []))} proposals")

        # Tasks
        data = _load_json("tasks.json")
        if not data:
            data = _load_json("github-tasks.json")
        for item in data.get("tasks", []):
            if not db.query(Task).filter(Task.id == item["id"]).first():
                db.add(Task(**_map_fields(item, _TASK_MAP, Task)))
        print(f"  Seeded {len(data.get('tasks', []))} tasks")

        # Launch Prep
        data = _load_json("launch-prep.json")
        for item in data.get("tasks", []):
            if not db.query(LaunchPrepTask).filter(LaunchPrepTask.id == item["id"]).first():
                db.add(LaunchPrepTask(**_map_fields(item, _LAUNCH_MAP, LaunchPrepTask)))
        print(f"  Seeded {len(data.get('tasks', []))} launch prep tasks")

        # Positioning (single row)
        data = _load_json("positioning.json")
        if data and not db.query(Positioning).filter(Positioning.id == "default").first():
            mapped = _map_fields(data, _POSITION_MAP, Positioning)
            mapped["id"] = "default"
            db.add(Positioning(**mapped))
            print("  Seeded positioning")

        # Security Audit
        data = _load_json("security-audit.json")
        for item in data.get("findings", []):
            if not db.query(SecurityFinding).filter(SecurityFinding.id == item["id"]).first():
                db.add(SecurityFinding(**_map_fields(item, _SECURITY_MAP, SecurityFinding)))
        print(f"  Seeded {len(data.get('findings', []))} security findings")

        db.commit()
        print("Seed complete.")

    finally:
        db.close()


if __name__ == "__main__":
    seed()
