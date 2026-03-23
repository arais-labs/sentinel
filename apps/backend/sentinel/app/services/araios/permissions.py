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


def combined_agent_permissions() -> dict[str, str]:
    """Return static defaults plus system-module action defaults."""
    permissions = dict(AGENT_PERMISSIONS)

    from app.services.araios.system_modules import get_system_modules

    for module in get_system_modules():
        for action in module.actions or []:
            if not action.handler:
                continue
            key = f"{module.name}.{action.id}"
            level = "approval" if action.approval else "allow"
            permissions.setdefault(key, level)

    return permissions
