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
from app.database.models import Module, SystemSetting

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


@router.get(
    "",
    summary="Agent system guide",
    description=(
        "Returns complete instructions for an agent: endpoints, module creation, "
        "and current module catalog."
    ),
)
async def agent_guide(
    db: Session = Depends(get_db),
    _=Depends(require_permission("manifest.read")),
):
    setting = db.query(SystemSetting).filter(SystemSetting.key == "manifest_base_url").first()
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
                "data": (
                    "Persistent records with full CRUD endpoints (e.g. leads, clients). "
                    "Can optionally include executable actions: "
                    "(a) record-scoped actions (placement='detail') appear as buttons on individual records "
                    "and receive the record context; "
                    "(b) standalone actions (placement='standalone') appear as callable action cards "
                    "in a dedicated Actions tab alongside the record list. "
                    "Use data+actions when you need both structured record storage AND "
                    "triggerable operations (e.g. a kanban board with 'Run fix' actions)."
                ),
                "tool": (
                    "Callable actions backed by sandboxed Python — no stored records (e.g. slack, weather). "
                    "Use tool when you only need to run operations, not store anything."
                ),
                "page": "UI-only module, no agent-facing endpoints.",
            },
        },

        # ── 2. SYSTEM ENDPOINTS ───────────────────────────────────────────────
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
                "method": "GET", "url": f"{base_url}/api/tasks",
                "description": "List collaborative tasks. Optional filters: ?client=&status=&owner=",
            },
            {
                "method": "POST", "url": f"{base_url}/api/tasks",
                "description": "Create a collaborative task.",
                "body": {
                    "title": "str",
                    "summary": "str",
                    "status": "backlog|todo|in_progress|in_review|blocked|handoff|done|cancelled (legacy also accepted)",
                    "priority": "low|medium|high|critical",
                    "owner": "str",
                    "handoffTo": "str",
                    "workPackage": "object (generic plan/artifacts; GitHub fields optional)",
                },
            },
            {
                "method": "PATCH", "url": f"{base_url}/api/tasks/:task_id",
                "description": "Update task fields and handoffs. workPackage is deep-merged.",
            },
            {
                "method": "DELETE", "url": f"{base_url}/api/tasks/:task_id",
                "description": "Delete a task (agent role usually requires approval).",
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

        # ── 3. MODULE ENGINE ENDPOINTS ─────────────────────────────────────────
        "module_engine": {
            "description": "Generic API for all data and tool modules. Replace :name with the module slug.",
            "endpoints": [
                {"method": "GET",    "url": f"{base_url}/api/modules",                                     "description": "List all registered modules."},
                {"method": "GET",    "url": f"{base_url}/api/modules/:name",                               "description": "Get full module config including fields, actions, and secrets schema."},
                {"method": "POST",   "url": f"{base_url}/api/modules",                                     "description": "Register a new module (see module_creation below). Subject to approval.", "body": "see module_creation.schema"},
                {"method": "PATCH",  "url": f"{base_url}/api/modules/:name",                               "description": "Patch module config fields. For 'actions', only referenced action IDs are updated; omitted actions are preserved."},
                {"method": "DELETE", "url": f"{base_url}/api/modules/:name",                               "description": "Delete a non-system module."},
                {"method": "GET",    "url": f"{base_url}/api/modules/:name/records",                       "description": "List records. Optional filters: ?filter_field=<field>&filter_value=<value>"},
                {"method": "POST",   "url": f"{base_url}/api/modules/:name/records",                       "description": "Create a record. Body is a flat JSON object matching the module's fields."},
                {"method": "GET",    "url": f"{base_url}/api/modules/:name/records/:id",                   "description": "Get a single record."},
                {"method": "PATCH",  "url": f"{base_url}/api/modules/:name/records/:id",                   "description": "Update a record (partial — send only changed fields)."},
                {"method": "DELETE", "url": f"{base_url}/api/modules/:name/records/:id",                   "description": "Delete a record."},
                {"method": "POST",   "url": f"{base_url}/api/modules/:name/action/:action_id",             "description": "Execute a standalone tool action (tool modules or data modules with placement='standalone'). Body: flat params object."},
                {"method": "POST",   "url": f"{base_url}/api/modules/:name/records/:id/action/:action_id", "description": "Execute a record-scoped action (data modules with placement='detail'). Receives record context. Body: flat params object."},
            ],
        },

        # ── 4. MODULE CREATION ─────────────────────────────────────────────────
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
                "icon":        "string — Lucide icon name (e.g. 'FileText', 'Box', 'Zap', 'Wrench').",
                "type":        "'data' | 'tool' — data stores records (optionally with actions), tool runs actions only.",
                "order":       "integer — sidebar position (lower = higher up).",
                "list_config": {
                    "titleField":    "string — field key to use as the record title in the list (e.g. 'title'). REQUIRED for data modules.",
                    "subtitleField": "string — field key shown as subtitle under the title (optional).",
                    "badgeField":    "string — field key rendered as a badge chip (e.g. 'status', 'priority') (optional).",
                    "filterField":   "string — field key used for the filter tab bar (e.g. 'status') (optional).",
                },
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
                        "id":          "string — action slug (e.g. 'send', 'fetch', 'run_fix')",
                        "label":       "string — display label",
                        "description": "string",
                        "placement": (
                            "'detail' | 'standalone' (default: 'standalone'). "
                            "detail: button on an individual record, receives record context in code. "
                            "standalone: callable action card in the Actions tab (tool modules always use standalone)."
                        ),
                        "params": [
                            {"key": "string", "label": "string", "type": "text|textarea|number", "required": "boolean", "placeholder": "string"}
                        ],
                        "code": (
                            "string — Python code executed in sandbox.\n"
                            "Available variables:\n"
                            "  params  : dict of user-supplied params\n"
                            "  secrets : dict of module secrets (set via UI, never exposed)\n"
                            "  record  : current record dict (record-scoped/detail actions only)\n"
                            "Available imports: httpx (pre-imported as http), json, re, math, base64, datetime\n"
                            "Set `result` dict before end — it is returned to the caller.\n"
                            "Example:\n"
                            "  r = await http.get('https://api.example.com', params=params, timeout=10)\n"
                            "  result = {'ok': True, 'data': r.json()}"
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
                "list_config": {"titleField": "client", "badgeField": "status", "filterField": "status"},
                "fields": [
                    {"key": "client",   "label": "Client",   "type": "text",   "required": True},
                    {"key": "amount",   "label": "Amount",   "type": "number", "required": True},
                    {"key": "status",   "label": "Status",   "type": "select", "options": ["draft", "sent", "paid"], "required": True},
                    {"key": "due_date", "label": "Due Date", "type": "date"},
                ],
                "actions": [], "secrets": [],
            },
            "tool_module_example": {
                "name": "exchange", "label": "Exchange Rates", "type": "tool", "icon": "TrendingUp", "order": 95,
                "description": "Get live currency exchange rates (no auth required)",
                "fields": [], "list_config": {}, "secrets": [],
                "actions": [
                    {
                        "id": "convert", "label": "Convert Currency",
                        "description": "Convert an amount from one currency to another",
                        "placement": "standalone",
                        "params": [
                            {"key": "from",   "label": "From",   "type": "text",   "required": True, "placeholder": "USD"},
                            {"key": "to",     "label": "To",     "type": "text",   "required": True, "placeholder": "EUR"},
                            {"key": "amount", "label": "Amount", "type": "number", "required": True},
                        ],
                        "code": (
                            "r = await http.get(f\"https://api.frankfurter.app/latest?from={params['from']}&to={params['to']}\", timeout=10)\n"
                            "data = r.json()\n"
                            "rate = data.get('rates', {}).get(params['to'])\n"
                            "result = {'ok': bool(rate), 'rate': rate, 'converted': round(float(params['amount']) * rate, 2) if rate else None}"
                        ),
                    }
                ],
            },
            "combined_data_and_actions_example": {
                "name": "kanban_improvements", "label": "Kanban Improvements", "type": "data",
                "icon": "Wrench", "order": 30,
                "description": "Track code improvement tasks with runnable fix actions per record.",
                "list_config": {"titleField": "title", "badgeField": "priority", "filterField": "status"},
                "fields": [
                    {"key": "title",       "label": "Title",     "type": "text",   "required": True},
                    {"key": "priority",    "label": "Priority",  "type": "badge",  "options": ["critical", "high", "medium", "low"], "required": True},
                    {"key": "status",      "label": "Status",    "type": "badge",  "options": ["todo", "in_progress", "in_review", "done", "wont_fix"], "required": True},
                    {"key": "category",    "label": "Category",  "type": "select", "options": ["security", "reliability", "code_quality", "ci_cd", "testing", "observability", "devex"], "required": True},
                    {"key": "area",        "label": "Code Area", "type": "text"},
                    {"key": "description", "label": "Description", "type": "textarea"},
                ],
                "actions": [
                    {
                        "id": "open_github_issue", "label": "Open GitHub Issue",
                        "description": "Creates a GitHub issue for this improvement item.",
                        "placement": "detail",
                        "params": [],
                        "code": (
                            "title = record.get('title', 'Untitled')\n"
                            "body = f\"**Priority:** {record.get('priority')}\\n**Area:** {record.get('area')}\\n\\n{record.get('description', '')}\"\n"
                            "r = await http.post(\n"
                            "    'https://api.github.com/repos/Domu-ai/kanban/issues',\n"
                            "    headers={'Authorization': f'token {secrets[\"github_token\"]}', 'Accept': 'application/vnd.github.v3+json'},\n"
                            "    json={'title': title, 'body': body, 'labels': [record.get('priority', 'medium')]},\n"
                            "    timeout=15,\n"
                            ")\n"
                            "data = r.json()\n"
                            "result = {'ok': r.status_code == 201, 'issue_url': data.get('html_url'), 'number': data.get('number')}"
                        ),
                    }
                ],
                "secrets": [
                    {"key": "github_token", "label": "GitHub Token", "required": True, "hint": "Personal access token with repo scope"}
                ],
            },
        },

        # ── 5. MODULE CATALOG ──────────────────────────────────────────────────
        "modules": catalog,
        "catalog_note": (
            f"Action code longer than {_CODE_TRUNCATE_THRESHOLD} chars is truncated. "
            "Call GET /api/modules/:name to retrieve the full module definition."
        ),
    }
