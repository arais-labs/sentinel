"""
GET /api/agent — single endpoint that tells an agent everything it needs to know:
  - how the system works
  - all available endpoints
  - how to create modules (schema + approval process)
  - current module list (actions truncated if code is large, with pointer to full detail)
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.middleware.auth import require_permission
from app.database.models import Module, Setting

router = APIRouter()

# If an action's code exceeds this length it gets truncated in the guide
_CODE_TRUNCATE_THRESHOLD = 300


def _maybe_truncate_action(action: dict, module_name: str, base_url: str) -> dict:
    code = action.get("code", "")
    if len(code) <= _CODE_TRUNCATE_THRESHOLD:
        return action
    short = {k: v for k, v in action.items() if k != "code"}
    short["code"] = f"[truncated — full code at GET {base_url}/api/modules/{module_name}]"
    return short


@router.get("", summary="Agent system guide", description="Returns complete instructions for an agent: auth, endpoints, module creation, and current module catalog.")
async def agent_guide(
    db: Session = Depends(get_db),
    _=Depends(require_permission("manifest.read")),
):
    setting = db.query(Setting).filter(Setting.key == "manifest_base_url").first()
    base_url = (setting.value if setting else "").rstrip("/")

    modules = db.query(Module).order_by(Module.order, Module.name).all()

    # Build truncated module catalog
    catalog = []
    for mod in modules:
        actions = []
        for action in (mod.actions or []):
            actions.append(_maybe_truncate_action(action, mod.name, base_url))

        entry = {
            "name": mod.name,
            "label": mod.label,
            "type": mod.type or "data",
            "description": mod.description or "",
            "is_system": mod.is_system,
        }
        if mod.type == "tool":
            entry["secrets_required"] = [s["key"] for s in (mod.secrets or []) if s.get("required")]
            entry["actions"] = actions
        else:
            entry["fields"] = mod.fields or []
            entry["actions"] = actions
        catalog.append(entry)

    return {

        # ── 1. SYSTEM OVERVIEW ──────────────────────────────────────────────────
        "system": {
            "name": "araiOS",
            "description": (
                "araiOS is a config-driven operator control plane. It stores data in typed "
                "modules and exposes a generic CRUD + action API. Agents interact exclusively "
                "through this API. New modules can be registered at runtime via the approval flow."
            ),
            "module_types": {
                "data": "Persistent records with CRUD endpoints (e.g. leads, clients).",
                "tool": "Callable actions backed by sandboxed Python — no stored records (e.g. slack, weather).",
                "page": "UI-only module, no agent-facing endpoints.",
            },
        },

        # ── 2. AUTH ─────────────────────────────────────────────────────────────
        "auth": {
            "scheme": "Bearer",
            "header": "Authorization: Bearer <your_agent_token>",
            "note": "Every request must carry your agent token. The token determines your permission level.",
            "permission_levels": {
                "allow":    "Request executes immediately and returns the result.",
                "approval": "Request is queued (HTTP 202). An admin must approve before it executes.",
                "deny":     "Request is rejected with HTTP 403.",
            },
        },

        # ── 3. SYSTEM ENDPOINTS ─────────────────────────────────────────────────
        "system_endpoints": [
            {
                "method": "GET", "url": f"{base_url}/api/agent",
                "description": "This guide. Call first to understand the system.",
            },
            {
                "method": "GET", "url": f"{base_url}/api/manifest",
                "description": "Machine-readable tool manifest (callable endpoints only, no code).",
            },
            {
                "method": "GET", "url": f"{base_url}/api/approvals",
                "description": "List all approval requests. Filter: ?status=pending|approved|rejected",
            },
            {
                "method": "POST", "url": f"{base_url}/api/approvals",
                "description": "Manually create an approval request for a custom action.",
                "body": {"action": "str", "resource": "str", "resourceId": "str|null", "description": "str", "payload": "object"},
            },
            {
                "method": "GET", "url": f"{base_url}/api/permissions",
                "description": "List all permission rules (action → allow|approval|deny).",
            },
            {
                "method": "GET", "url": f"{base_url}/api/documents",
                "description": "List documents.",
            },
            {
                "method": "GET", "url": f"{base_url}/api/github-tasks",
                "description": "List GitHub tasks.",
            },
            {
                "method": "GET", "url": f"{base_url}/api/coordination",
                "description": "List coordination messages between agents.",
            },
            {
                "method": "POST", "url": f"{base_url}/api/coordination",
                "description": "Post a coordination message.",
                "body": {"from_agent": "str", "to_agent": "str", "message": "str"},
            },
        ],

        # ── 4. MODULE ENGINE ENDPOINTS ──────────────────────────────────────────
        "module_engine": {
            "description": "Generic API for all data and tool modules. Replace :name with the module slug.",
            "endpoints": [
                {"method": "GET",    "url": f"{base_url}/api/modules",                                     "description": "List all registered modules."},
                {"method": "GET",    "url": f"{base_url}/api/modules/:name",                               "description": "Get full module config including fields, actions, and secrets schema."},
                {"method": "POST",   "url": f"{base_url}/api/modules",                                     "description": "Register a new module (see module_creation below). Subject to approval.", "body": "see module_creation.schema"},
                {"method": "PATCH",  "url": f"{base_url}/api/modules/:name",                               "description": "Update a module config (label, fields, actions, etc.)."},
                {"method": "DELETE", "url": f"{base_url}/api/modules/:name",                               "description": "Delete a non-system module."},
                {"method": "GET",    "url": f"{base_url}/api/modules/:name/records",                       "description": "List records. Optional filters: ?filter_field=<field>&filter_value=<value>"},
                {"method": "POST",   "url": f"{base_url}/api/modules/:name/records",                       "description": "Create a record. Body is a flat JSON object matching the module's fields."},
                {"method": "GET",    "url": f"{base_url}/api/modules/:name/records/:id",                   "description": "Get a single record."},
                {"method": "PATCH",  "url": f"{base_url}/api/modules/:name/records/:id",                   "description": "Update a record (partial — send only changed fields)."},
                {"method": "DELETE", "url": f"{base_url}/api/modules/:name/records/:id",                   "description": "Delete a record."},
                {"method": "POST",   "url": f"{base_url}/api/modules/:name/action/:action_id",             "description": "Execute a tool action. Body: {params: {key: value}}. Returns {ok, result}."},
                {"method": "POST",   "url": f"{base_url}/api/modules/:name/records/:id/action/:action_id", "description": "Execute a record-scoped action (e.g. send a lead to Slack)."},
            ],
        },

        # ── 5. MODULE CREATION ──────────────────────────────────────────────────
        "module_creation": {
            "process": [
                "1. POST /api/modules with the module definition below.",
                "2. If modules.create permission is 'approval', you receive HTTP 202 and an approval_id.",
                "3. An admin approves the request in the Approvals UI.",
                "4. The module is created and immediately available in GET /api/modules.",
                "5. Permissions for its actions are auto-seeded (default: allow).",
            ],
            "schema": {
                "name":        "string — unique slug, lowercase, no spaces (e.g. 'invoices'). REQUIRED.",
                "label":       "string — display name (e.g. 'Invoices'). REQUIRED.",
                "description": "string — what this module does.",
                "icon":        "string — Lucide icon name (e.g. 'FileText', 'Box', 'Zap').",
                "type":        "'data' | 'tool' — data stores records, tool runs Python actions.",
                "order":       "integer — sidebar position (lower = higher up).",
                "fields": [
                    {
                        "key":         "string — field identifier (snake_case)",
                        "label":       "string — display label",
                        "type":        "'text' | 'textarea' | 'email' | 'url' | 'number' | 'date' | 'select' | 'badge' | 'tags' | 'readonly'",
                        "required":    "boolean",
                        "options":     "string[] — only for type=select or badge",
                        "placeholder": "string — optional hint",
                    }
                ],
                "actions": [
                    {
                        "id":          "string — action slug (e.g. 'send', 'fetch')",
                        "label":       "string — display label",
                        "description": "string",
                        "params": [
                            {"key": "string", "label": "string", "type": "text|textarea|number", "required": "boolean", "placeholder": "string"}
                        ],
                        "code": (
                            "string — Python code executed in sandbox.\n"
                            "Available variables:\n"
                            "  params  : dict of user-supplied params\n"
                            "  secrets : dict of module secrets (set via UI, never exposed)\n"
                            "  record  : current record dict (record-scoped actions only)\n"
                            "Available imports: httpx (pre-imported)\n"
                            "Set `result` dict before end — it is returned to the caller.\n"
                            "Example:\n"
                            "  import httpx\n"
                            "  r = httpx.get('https://api.example.com', params=params, timeout=10).json()\n"
                            "  result = {'ok': True, 'data': r}"
                        ),
                    }
                ],
                "secrets": [
                    {"key": "string", "label": "string", "required": "boolean", "hint": "string"}
                ],
            },
            "data_module_example": {
                "name": "invoices", "label": "Invoices", "type": "data", "icon": "FileText", "order": 60,
                "description": "Track customer invoices",
                "fields": [
                    {"key": "client",  "label": "Client",  "type": "text",   "required": True},
                    {"key": "amount",  "label": "Amount",  "type": "number", "required": True},
                    {"key": "status",  "label": "Status",  "type": "select", "options": ["draft", "sent", "paid"], "required": True},
                    {"key": "due_date","label": "Due Date","type": "date"},
                ],
                "actions": [], "secrets": [], "list_config": {},
            },
            "tool_module_example": {
                "name": "exchange", "label": "Exchange Rates", "type": "tool", "icon": "TrendingUp", "order": 95,
                "description": "Get live currency exchange rates (no auth required)",
                "fields": [], "list_config": {},
                "secrets": [],
                "actions": [
                    {
                        "id": "convert", "label": "Convert Currency",
                        "description": "Convert an amount from one currency to another",
                        "params": [
                            {"key": "from", "label": "From", "type": "text", "required": True, "placeholder": "USD"},
                            {"key": "to",   "label": "To",   "type": "text", "required": True, "placeholder": "EUR"},
                            {"key": "amount","label": "Amount","type": "number","required": True},
                        ],
                        "code": (
                            "import httpx\n"
                            "r = httpx.get(f\"https://api.frankfurter.app/latest?from={params['from']}&to={params['to']}\", timeout=10).json()\n"
                            "rate = r.get('rates', {}).get(params['to'])\n"
                            "result = {'ok': bool(rate), 'rate': rate, 'converted': round(float(params['amount']) * rate, 2) if rate else None}"
                        ),
                    }
                ],
            },
        },

        # ── 6. MODULE CATALOG ───────────────────────────────────────────────────
        "modules": catalog,
        "catalog_note": (
            f"Action code longer than {_CODE_TRUNCATE_THRESHOLD} chars is truncated. "
            "Call GET /api/modules/:name to retrieve the full module definition."
        ),
    }
