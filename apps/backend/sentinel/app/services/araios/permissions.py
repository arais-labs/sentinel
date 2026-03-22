"""Default permission map seeded on startup.

Module-specific permissions are seeded dynamically by routers/araios/modules.py.
"allow"    → execute normally
"approval" → create an approval record, return 202
"deny"     → return 403
"""

AGENT_PERMISSIONS: dict[str, str] = {
    "tasks.list": "allow",
    "tasks.create": "allow",
    "tasks.update": "allow",
    "tasks.delete": "approval",
    "documents.list": "allow",
    "documents.create": "approval",
    "documents.update": "approval",
    "documents.delete": "approval",
    "approvals.list": "allow",
    "approvals.create": "allow",
    "approvals.resolve": "deny",
    "modules.list": "allow",
    "modules.create": "approval",
    "modules.update": "approval",
    "modules.delete": "approval",
    "manifest.read": "allow",
    "settings.manage": "deny",
}
