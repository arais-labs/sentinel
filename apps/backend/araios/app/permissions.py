# Per-action permission map seeded on startup.
# Module-specific permissions are seeded dynamically by _seed_module_permissions.
# "allow"    → execute normally
# "approval" → create an approval record, return 202
# "deny"     → return 403

AGENT_PERMISSIONS: dict[str, str] = {
    # Native Tasks plugin — collaborative by default
    "tasks.list": "allow",
    "tasks.create": "allow",
    "tasks.update": "allow",
    "tasks.delete": "approval",

    # Documents (custom system page)
    "documents.list":   "allow",
    "documents.create": "approval",
    "documents.update": "approval",
    "documents.delete": "approval",

    # Approvals — agent can list and create, not resolve
    "approvals.list":   "allow",
    "approvals.create": "allow",
    "approvals.resolve": "deny",

    # Module engine — list/read free, mutations go through approval
    "modules.list":   "allow",
    "modules.create": "approval",
    "modules.update": "approval",
    "modules.delete": "approval",
}
