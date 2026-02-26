from __future__ import annotations

import os
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Memory, Message, SessionSummary
from app.services.memory_search import MemorySearchService
from app.services.skills.registry import SkillRegistry
from app.services.llm.types import (
    AgentMessage,
    AssistantMessage,
    ImageContent,
    SystemMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)

logger = logging.getLogger(__name__)


class ContextBuilder:
    def __init__(
        self,
        *,
        default_system_prompt: str | None = None,
        message_limit: int = 50,
        skill_registry: SkillRegistry | None = None,
        available_tools: set[str] | None = None,
        env: Mapping[str, str] | None = None,
        memory_search_service: MemorySearchService | None = None,
    ) -> None:
        self._default_system_prompt = (
            default_system_prompt or settings.default_system_prompt
        ).strip()
        self._message_limit = max(1, message_limit)
        self._skill_registry = skill_registry
        self._available_tools = set(available_tools or set())
        self._env = dict(env or os.environ)
        self._memory_search_service = memory_search_service

    async def build(
        self,
        db: AsyncSession,
        session_id: UUID,
        system_prompt: str | None = None,
        pending_user_message: str | None = None,
    ) -> list[AgentMessage]:
        prompt = (system_prompt or self._default_system_prompt).strip()
        prompt += f"\n\nCurrent date and time: {datetime.now(UTC).strftime('%A, %B %d, %Y at %H:%M UTC')}"
        prompt += f"\nYour current session ID is: {session_id}"
        context: list[AgentMessage] = [SystemMessage(content=prompt)]
        delegation_policy = self._delegation_system_message()
        if delegation_policy is not None:
            context.append(delegation_policy)
        trigger_policy = self._trigger_system_message()
        if trigger_policy is not None:
            context.append(trigger_policy)
        execution_policy = self._execution_system_message()
        if execution_policy is not None:
            context.append(execution_policy)
        browser_policy = self._browser_automation_system_message()
        if browser_policy is not None:
            context.append(browser_policy)

        context.extend(await self._memory_system_messages(db, pending_user_message))

        summary = await self._latest_summary(db, session_id)
        if summary:
            context.append(SystemMessage(content=summary))

        context.extend(self._active_skill_messages())

        recent = await self._recent_messages(db, session_id)
        estimated_tokens = int(
            sum(self._word_count(item.content or "") for item in recent) * 1.3
        )
        if estimated_tokens > 80_000:
            logger.warning(
                "Context window estimate exceeds threshold: session_id=%s estimated_tokens=%s",
                session_id,
                estimated_tokens,
            )
        context.extend(self._convert_history_messages(recent))
        return context

    def _delegation_system_message(self) -> SystemMessage | None:
        available = self._available_tools
        can_delegate = "spawn_sub_agent" in available or "pythonXagent" in available
        if not can_delegate:
            return None

        return SystemMessage(
            content=(
                "## Delegation Policy\n"
                "Prefer delegation for bounded one-off tasks that mostly produce inputs for later steps "
                "(research, data collection, endpoint inspection, broad scans, option gathering).\n"
                "Keep continuity-heavy tasks in the main loop when they require evolving user context or direct reasoning continuity.\n"
                "When delegating: define a narrow objective, set explicit scope, restrict allowed tools, and keep max steps conservative.\n"
                "After delegation, verify with check_sub_agent and integrate only the useful outputs into the main plan."
            )
        )

    def _trigger_system_message(self) -> SystemMessage | None:
        if "trigger_create" not in self._available_tools:
            return None

        return SystemMessage(
            content=(
                "## Trigger Automation Policy\n"
                "You have trigger management tools: trigger_create, trigger_list, trigger_update, trigger_delete.\n"
                "**Proactively suggest creating triggers** when the user describes any of these patterns:\n"
                "- Recurring tasks: monitoring, reports, reminders, data collection, health checks\n"
                "- Scheduled actions: 'every morning', 'once a day', 'every hour', 'weekly'\n"
                "- Conditional checks: 'keep an eye on', 'let me know if', 'watch for'\n\n"
                "When creating agent_message triggers, ALWAYS set action_config.session_id to your current session ID "
                "so the trigger fires into this conversation and results appear here.\n"
                "After creating a trigger, store its trigger_id in memory so you can manage it later.\n"
                "Common cron patterns: '0 9 * * MON-FRI' (weekday 9am), '*/30 * * * *' (every 30 min), "
                "'0 */2 * * *' (every 2 hours), '0 0 * * *' (midnight daily)."
            )
        )

    def _execution_system_message(self) -> SystemMessage | None:
        return SystemMessage(
            content=(
                "## Execution Policy\n"
                "When the user asks you to execute a multi-step task, keep acting until the task is complete or a true blocker appears.\n"
                "Do not end a turn with text like 'I'll do X next' while no new tool call is issued.\n"
                "If a tool fails, immediately try a different valid approach (e.g., alternate selector strategy) before asking the user for help.\n"
                "Only ask the user for input when required by external verification, permissions, or unavailable credentials."
            )
        )

    def _browser_automation_system_message(self) -> SystemMessage | None:
        available = self._available_tools
        if "browser_navigate" not in available or "browser_snapshot" not in available:
            return None

        return SystemMessage(
            content=(
                "## Browser Automation Playbook\n"
                "Use this standard flow for web tasks: navigate -> snapshot(interactive_only=true) -> interact -> verify -> continue.\n"
                "For standard multi-field forms, prefer browser_fill_form with ordered steps to reduce tool-call overhead.\n"
                "Use selectors exactly as returned by browser_snapshot (for example: 'button: Continue', 'textbox: Email', 'combobox: Month').\n"
                "For dropdowns/selects, use browser_select instead of clicking option rows.\n"
                "Before clicking a submit/next button, use browser_wait_for with condition='enabled'.\n"
                "After filling fields, verify with browser_get_value or a fresh browser_snapshot.\n"
                "When navigation/popups create multiple tabs, use browser_tabs then browser_tab_focus before continuing.\n"
                "Only stop for user help when external human verification is required (captcha, OTP, email code, phone code)."
            )
        )

    async def _latest_summary(self, db: AsyncSession, session_id: UUID) -> str | None:
        result = await db.execute(
            select(SessionSummary).where(SessionSummary.session_id == session_id)
        )
        summaries = result.scalars().all()
        if not summaries:
            return None
        summaries.sort(
            key=lambda item: item.created_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        payload = summaries[0].summary if isinstance(summaries[0].summary, dict) else {}
        summary_text = str(payload.get("summary_text") or "").strip()
        if not summary_text:
            return None
        return f"Session summary:\n{summary_text}"

    async def _recent_messages(
        self, db: AsyncSession, session_id: UUID
    ) -> list[Message]:
        result = await db.execute(
            select(Message).where(Message.session_id == session_id)
        )
        items = result.scalars().all()
        items.sort(key=lambda item: item.created_at or datetime.min.replace(tzinfo=UTC))
        return items[-self._message_limit :]

    _MAX_TOOL_RESULT_CHARS = 4000

    def _convert_message(self, message: Message) -> AgentMessage:
        return self._convert_message_with_options(message, include_tool_calls=True)

    def _convert_message_with_options(
        self,
        message: Message,
        *,
        include_tool_calls: bool,
    ) -> AgentMessage:
        if message.role == "assistant":
            text = (message.content or "").strip()
            content_blocks: list[TextContent | ToolCallContent] = []
            if text:
                content_blocks.append(TextContent(text=text))
            if include_tool_calls:
                metadata = message.metadata_json or {}
                for tc in metadata.get("tool_calls") or []:
                    if isinstance(tc, dict) and tc.get("id"):
                        content_blocks.append(
                            ToolCallContent(
                                id=tc["id"],
                                name=tc.get("name", ""),
                                arguments=(
                                    tc.get("arguments")
                                    if isinstance(tc.get("arguments"), dict)
                                    else {}
                                ),
                            )
                        )
            return AssistantMessage(content=content_blocks)
        if message.role in {"tool", "tool_result"}:
            metadata = message.metadata_json or {}
            content = self._truncate_tool_result(message.content or "")
            return ToolResultMessage(
                tool_call_id=message.tool_call_id or "",
                tool_name=message.tool_name or "",
                content=content,
                is_error=bool(metadata.get("is_error")),
            )
        if message.role == "system":
            return SystemMessage(content=message.content or "")
        metadata = message.metadata_json or {}
        attachments = metadata.get("attachments")
        blocks: list[TextContent | ImageContent] = []
        text = (message.content or "").strip()
        if text:
            blocks.append(TextContent(text=text))
        if isinstance(attachments, list):
            for item in attachments:
                if not isinstance(item, dict):
                    continue
                mime_type = item.get("mime_type")
                data = item.get("base64")
                if not isinstance(mime_type, str) or not isinstance(data, str):
                    continue
                if not data.strip():
                    continue
                blocks.append(ImageContent(media_type=mime_type.strip() or "image/png", data=data.strip()))
        if blocks:
            return UserMessage(content=blocks)
        return UserMessage(content=message.content or "")

    def _truncate_tool_result(self, content: str) -> str:
        """Truncate large tool results (e.g. base64 screenshots) to avoid context overflow."""
        if len(content) <= self._MAX_TOOL_RESULT_CHARS:
            return content
        # Try to parse as JSON and strip large base64 fields
        try:
            parsed = __import__("json").loads(content)
            if isinstance(parsed, dict):
                for key in list(parsed.keys()):
                    val = parsed[key]
                    if isinstance(val, str) and len(val) > 500:
                        parsed[key] = f"[truncated: {len(val)} chars]"
                return __import__("json").dumps(parsed)
        except Exception:
            pass
        return (
            content[: self._MAX_TOOL_RESULT_CHARS]
            + f"\n...[truncated from {len(content)} chars]"
        )

    def _active_skill_messages(self) -> list[SystemMessage]:
        if self._skill_registry is None:
            return []

        messages: list[SystemMessage] = []
        active_skills = self._skill_registry.list_active(
            self._available_tools, self._env
        )
        for skill in active_skills:
            messages.append(
                SystemMessage(
                    content=f"## Active Skill: {skill.name}\n\n{skill.system_prompt_injection}",
                )
            )
        return messages

    def _word_count(self, text: str) -> int:
        return len([part for part in text.split() if part])

    def _convert_history_messages(self, messages: list[Message]) -> list[AgentMessage]:
        converted: list[AgentMessage] = []
        i = 0
        total = len(messages)
        while i < total:
            current = messages[i]
            if current.role == "assistant":
                tool_call_ids = self._tool_call_ids(current)
                if not tool_call_ids:
                    converted.append(
                        self._convert_message_with_options(
                            current, include_tool_calls=True
                        )
                    )
                    i += 1
                    continue

                j = i + 1
                trailing_tool_results: list[Message] = []
                while j < total and messages[j].role in {"tool", "tool_result"}:
                    trailing_tool_results.append(messages[j])
                    j += 1

                result_ids = {
                    item.tool_call_id
                    for item in trailing_tool_results
                    if isinstance(item.tool_call_id, str) and item.tool_call_id
                }
                has_all_results = all(
                    call_id in result_ids for call_id in tool_call_ids
                )

                if has_all_results:
                    converted.append(
                        self._convert_message_with_options(
                            current, include_tool_calls=True
                        )
                    )
                    converted.extend(
                        self._convert_message(item) for item in trailing_tool_results
                    )
                else:
                    # Anthropic requires strict adjacency between tool_use and tool_result.
                    # If history is truncated/corrupt, degrade to plain assistant text.
                    converted.append(
                        self._convert_message_with_options(
                            current, include_tool_calls=False
                        )
                    )
                i = j if trailing_tool_results else i + 1
                continue

            if current.role in {"tool", "tool_result"}:
                # Skip orphan tool results not attached to an assistant tool_use turn.
                i += 1
                continue

            converted.append(self._convert_message(current))
            i += 1
        return converted

    def _tool_call_ids(self, message: Message) -> list[str]:
        metadata = message.metadata_json or {}
        ids: list[str] = []
        for item in metadata.get("tool_calls") or []:
            if not isinstance(item, dict):
                continue
            call_id = item.get("id")
            if isinstance(call_id, str) and call_id:
                ids.append(call_id)
        return ids

    async def _memory_system_messages(
        self,
        db: AsyncSession,
        pending_user_message: str | None,
    ) -> list[SystemMessage]:
        result = await db.execute(select(Memory))
        memories = result.scalars().all()
        if not memories:
            return []

        roots = [item for item in memories if item.parent_id is None]
        if not roots:
            return []

        roots.sort(
            key=lambda item: (
                bool(item.pinned),
                int(item.importance or 0),
                item.last_accessed_at
                or item.updated_at
                or item.created_at
                or datetime.min.replace(tzinfo=UTC),
            ),
            reverse=True,
        )

        pinned_roots = [r for r in roots if r.pinned]
        unpinned_roots = [r for r in roots if not r.pinned]

        # Pinned roots: inject full content directly — no tool call needed to read them
        pinned_messages: list[SystemMessage] = []
        for root in pinned_roots:
            title = (root.title or "").strip() or root.content.strip()[:80]
            pinned_messages.append(
                SystemMessage(
                    content=f"## Memory (pinned): {title}\n\n{root.content.strip()}"
                )
            )

        # Non-pinned roots: list with summary so agent knows they exist
        root_lines: list[str] = []
        for root in unpinned_roots:
            title = (root.title or "").strip() or root.content.strip()[:80]
            summary = (root.summary or "").strip() or root.content.strip()[:140]
            root_lines.append(
                f"- [{root.id}] title={title} | summary={summary} | importance={int(root.importance or 0)}"
            )

        guidance = (
            "## Hierarchical Memory Policy\n"
            "Pinned memories are already fully injected above — do NOT call memory tools to re-fetch them.\n"
            "Non-pinned root memories are listed below (summary only). Use memory_search or memory_get_node to retrieve their full content when relevant.\n\n"
            "### Depth Strategy (store)\n"
            "- Depth 0 (root): stable, high-value anchors (identity, long-lived project truths, durable constraints).\n"
            "- Depth 1: major subtopics under a root (workstreams, key decisions, recurring patterns).\n"
            "- Depth 2+: granular details (evidence, examples, implementation specifics, one-off observations).\n"
            "When storing detailed information, prefer attaching it as a child via parent_id instead of creating new roots.\n\n"
            "### Depth Strategy (retrieve)\n"
            "- Start with memory_search for relevance.\n"
            "- If a result may have deeper detail, automatically traverse using memory_get_node + memory_list_children.\n"
            "- Stop traversal when confidence is high or deeper nodes stop adding relevant signal."
        )

        index_block = f"{guidance}\n\n## Non-Pinned Root Memories\n" + (
            "\n".join(root_lines) if root_lines else "(none)"
        )
        messages = [*pinned_messages, SystemMessage(content=index_block)]

        query = (pending_user_message or "").strip()
        if not query or self._memory_search_service is None:
            return messages

        try:
            hits = await self._memory_search_service.search(db, query, limit=12)
        except Exception:  # noqa: BLE001
            hits = []
        if not hits:
            return messages

        by_id = {item.id: item for item in memories}
        children_by_parent: dict[UUID | None, list[Memory]] = {}
        for item in memories:
            children_by_parent.setdefault(item.parent_id, []).append(item)

        relevant_lines: list[str] = []
        seen: set[UUID] = set()
        for hit in hits:
            node = hit.memory
            if node.id in seen:
                continue
            seen.add(node.id)
            root = self._resolve_root(node, by_id)
            node_depth = self._memory_depth(node, by_id)
            title = (node.title or "").strip() or node.content.strip()[:80]
            summary = (node.summary or "").strip() or node.content.strip()[:160]
            relevant_lines.append(
                f"- match=[{node.id}] root=[{root.id}] depth={node_depth} score={hit.score:.4f} title={title} summary={summary}"
            )
            for child in children_by_parent.get(node.id, []):
                if child.id in seen:
                    continue
                seen.add(child.id)
                child_depth = self._memory_depth(child, by_id)
                child_title = (child.title or "").strip() or child.content.strip()[:80]
                child_summary = (child.summary or "").strip() or child.content.strip()[
                    :120
                ]
                relevant_lines.append(
                    f"  child=[{child.id}] depth={child_depth} title={child_title} summary={child_summary}"
                )

        if relevant_lines:
            messages.append(
                SystemMessage(
                    content="## Potentially Relevant Memory Branches (auto)\n"
                    + "\n".join(relevant_lines)
                )
            )
        return messages

    def _resolve_root(self, node: Memory, by_id: dict[UUID, Memory]) -> Memory:
        current = node
        guard: set[UUID] = set()
        while (
            current.parent_id
            and current.parent_id in by_id
            and current.parent_id not in guard
        ):
            guard.add(current.parent_id)
            current = by_id[current.parent_id]
        return current

    def _memory_depth(self, node: Memory, by_id: dict[UUID, Memory]) -> int:
        depth = 0
        current = node
        guard: set[UUID] = set()
        while (
            current.parent_id
            and current.parent_id in by_id
            and current.parent_id not in guard
        ):
            guard.add(current.parent_id)
            depth += 1
            current = by_id[current.parent_id]
        return depth
