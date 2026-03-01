from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.dependencies import get_db
from app.middleware.auth import require_permission
from app.database.models import Module, Permission, SystemSetting

router = APIRouter()


@router.get("")
async def get_manifest(db: Session = Depends(get_db), _=Depends(require_permission("manifest.read"))):
    # Get base_url from settings
    setting = db.query(SystemSetting).filter(SystemSetting.key == "manifest_base_url").first()
    base_url = (setting.value if setting else "").rstrip("/")

    # Get all permissions
    perms = {p.action: p.level for p in db.query(Permission).all()}

    modules = db.query(Module).order_by(Module.order, Module.name).all()

    manifest_modules = []
    for mod in modules:
        # Skip if list is denied
        if perms.get(f"{mod.name}.list") == "deny":
            continue

        m = {
            "name": mod.name,
            "label": mod.label,
            "type": mod.type or "data",
            "description": mod.description or "",
        }

        endpoints = []

        if mod.type == "tool":
            # Tool modules: just action endpoints
            for action in (mod.actions or []):
                path = f"/api/modules/{mod.name}/action/{action['id']}"
                ep = {
                    "action": action["id"],
                    "label": action.get("label", action["id"]),
                    "description": action.get("description", ""),
                    "method": "POST",
                    "url": base_url + path,
                    "body": {
                        p["key"]: {
                            "type": p.get("type", "text"),
                            "required": p.get("required", False),
                            "description": p.get("label", p["key"]),
                            **({"placeholder": p["placeholder"]} if p.get("placeholder") else {}),
                        }
                        for p in (action.get("params") or [])
                    }
                }
                endpoints.append(ep)
        else:
            # Data / page modules: CRUD endpoints
            base = f"/api/modules/{mod.name}"
            endpoints = [
                {"action": "list",   "method": "GET",    "url": base_url + base + "/records",      "description": f"List all {mod.label} records. Optional query: ?filter_field=&filter_value="},
                {"action": "get",    "method": "GET",    "url": base_url + base + "/records/{id}", "description": f"Get a single {mod.label} record"},
                {"action": "create", "method": "POST",   "url": base_url + base + "/records",      "description": f"Create a new {mod.label} record", "body": {f["key"]: {"type": f.get("type", "text"), "required": f.get("required", False)} for f in (mod.fields or [])}},
                {"action": "update", "method": "PATCH",  "url": base_url + base + "/records/{id}", "description": f"Update a {mod.label} record (partial)"},
                {"action": "delete", "method": "DELETE", "url": base_url + base + "/records/{id}", "description": f"Delete a {mod.label} record"},
            ]
            # Add custom actions
            for action in (mod.actions or []):
                if action.get("type") in ("create", "delete"):
                    continue  # already covered above
                if action.get("code"):
                    path = f"/api/modules/{mod.name}/records/{{id}}/action/{action['id']}"
                    endpoints.append({
                        "action": action["id"],
                        "label": action.get("label"),
                        "description": action.get("description", ""),
                        "method": "POST",
                        "url": base_url + path,
                        "body": {p["key"]: {"type": p.get("type", "text"), "required": p.get("required", False)} for p in (action.get("params") or [])}
                    })

        m["endpoints"] = endpoints
        manifest_modules.append(m)

    return {
        "auth": {
            "scheme": "Bearer",
            "header": "Authorization: Bearer <your_agent_token>",
            "note": "Every request must include your agent token in the Authorization header"
        },
        "base_url": base_url,
        "modules": manifest_modules,
    }
