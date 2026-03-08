from __future__ import annotations

import fnmatch
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import GitAccount
from app.services.tools.executor import ToolValidationError
from app.services.tools.registry import ToolDefinition


def git_accounts_available_tool(
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        host_filter_raw = payload.get("host")
        repo_url_raw = payload.get("repo_url")
        require_write_raw = payload.get("require_write", False)

        if host_filter_raw is not None and (
            not isinstance(host_filter_raw, str) or not host_filter_raw.strip()
        ):
            raise ToolValidationError("Field 'host' must be a non-empty string when provided")
        if repo_url_raw is not None and (
            not isinstance(repo_url_raw, str) or not repo_url_raw.strip()
        ):
            raise ToolValidationError("Field 'repo_url' must be a non-empty string when provided")
        if not isinstance(require_write_raw, bool):
            raise ToolValidationError("Field 'require_write' must be a boolean")

        host_filter = host_filter_raw.strip().lower() if isinstance(host_filter_raw, str) else None
        repo_ref = _parse_repo_ref(repo_url_raw) if isinstance(repo_url_raw, str) else None

        async with session_factory() as db:
            result = await db.execute(select(GitAccount))
            accounts = result.scalars().all()

        accounts.sort(
            key=lambda item: (
                item.updated_at or datetime.min.replace(tzinfo=UTC),
                (item.name or "").casefold(),
            ),
            reverse=True,
        )

        entries: list[dict[str, Any]] = []
        for item in accounts:
            host = (item.host or "").strip().lower()
            if host_filter and host != host_filter:
                continue
            matches_repo = _matches_repo(item=item, repo=repo_ref)
            if repo_ref is not None and not matches_repo:
                continue
            if require_write_raw and not (item.token_write or "").strip():
                continue
            if (not require_write_raw) and not (item.token_read or "").strip():
                continue
            entries.append(
                {
                    "id": str(item.id),
                    "name": item.name,
                    "host": item.host,
                    "scope_pattern": item.scope_pattern,
                    "author_name": item.author_name,
                    "author_email": item.author_email,
                    "has_read_token": bool((item.token_read or "").strip()),
                    "has_write_token": bool((item.token_write or "").strip()),
                    "matches_repo": matches_repo if repo_ref is not None else None,
                    "created_at": item.created_at.isoformat() if item.created_at else None,
                    "updated_at": item.updated_at.isoformat() if item.updated_at else None,
                }
            )

        return {
            "total": len(entries),
            "require_write": require_write_raw,
            "repo_target": repo_ref["target"] if repo_ref is not None else None,
            "accounts": entries,
        }

    return ToolDefinition(
        name="git_accounts_available",
        description=(
            "List configured git accounts available to the agent (no secret tokens returned). "
            "Optional filters support host, repo_url scope match, and read/write requirement."
        ),
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "host": {
                    "type": "string",
                    "description": "Optional host filter (for example: github.com)",
                },
                "repo_url": {
                    "type": "string",
                    "description": "Optional repository URL to filter by scope matching",
                },
                "require_write": {
                    "type": "boolean",
                    "description": "If true, only accounts with a write token are returned",
                },
            },
        },
        execute=_execute,
    )


def _parse_repo_ref(repo_url: str) -> dict[str, str]:
    raw = repo_url.strip()
    if not raw:
        raise ToolValidationError("Field 'repo_url' must be non-empty")

    host = ""
    path = ""
    if "://" in raw:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").strip().lower()
        path = (parsed.path or "").strip().lstrip("/")
    elif raw.startswith("git@") and ":" in raw:
        host = raw[4:].split(":", 1)[0].strip().lower()
        path = raw.split(":", 1)[1].strip().lstrip("/")
    else:
        raise ToolValidationError("Field 'repo_url' must be a supported git URL")

    if path.endswith(".git"):
        path = path[:-4]
    path = path.strip("/")
    if not host or not path:
        raise ToolValidationError("Field 'repo_url' must include host and repository path")
    target = f"{host}/{path.lower()}"
    return {"host": host, "path": path.lower(), "target": target}


def _matches_repo(*, item: GitAccount, repo: dict[str, str] | None) -> bool:
    if repo is None:
        return True
    host = (item.host or "").strip().lower()
    if host != repo["host"]:
        return False
    pattern = (item.scope_pattern or "*").strip().lower() or "*"
    return fnmatch.fnmatch(repo["target"], pattern) or fnmatch.fnmatch(repo["path"], pattern)
