import asyncio
import logging
from collections import defaultdict, deque
from collections.abc import Awaitable
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import FastAPI

from app.logging_context import configure_logging

# Configure logging so our debug/info logs are visible and session-scoped.
configure_logging()
logger = logging.getLogger(__name__)
from fastapi.middleware.cors import CORSMiddleware

from app.sentral import ConversationItem, GenerationConfig, RunTurnRequest, TextBlock
from app.config import settings
from app.database import AsyncSessionLocal, init_db
from app.middleware import (
    RateLimitMiddleware,
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
    register_error_handlers,
)
from app.routers import (
    admin,
    agent_modes as agent_modes_router,
    approvals as approvals_router,
    auth,
    git as git_router,
    health,
    memory,
    models,
    onboarding,
    runtime,
    settings as settings_router,
    sessions,
    sessions_compaction,
    sub_agents,
    telegram,
    triggers,
    vnc_proxy,
    ws,
    webhooks,
)
from app.routers.araios import api_router as araios_api_router
from app.services.agent import ContextBuilder, SentinelRuntimeSupport
from app.services.agent_runtime_adapters import SentinelLoopRuntimeAdapter, runtime_event_to_sentinel_event
from app.services.sessions.agent_run_registry import AgentRunRegistry
from app.services.araios.runtime_services import configure_runtime_services
from app.services.memory.embeddings import EmbeddingService
from app.services.llm.factory import build_tier_provider_from_settings
from app.services.llm.ids import TierName
from app.services.memory.backfill import run_memory_embedding_backfill
from app.services.memory.search import MemorySearchService
from app.services.runtime.terminal_manager import (
    configure_terminal_completion_handler,
    configure_terminal_event_broadcaster,
    get_terminal_manager,
)
from app.services.runtime.session_runtime import (
    configure_runtime_job_completion_callback,
    run_session_runtime_janitor,
)
from app.services.sessions.session_naming import SessionNamingService
from app.services.sub_agents import SubAgentOrchestrator
from app.services.tools import ToolExecutor
from app.services.tools.approval import ApprovalService
from app.services.tools.approval.approval_waiters import (
    build_tool_db_approval_result_recorder,
    build_tool_db_approval_waiter,
)
from app.services.tools.runtime_registry import build_runtime_registry
from app.services.browser.pool import BrowserPool
from app.services.triggers.trigger_scheduler import TriggerScheduler
from app.services.ws.ws_manager import ConnectionManager


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop_event = asyncio.Event()
    cleanup_task = asyncio.create_task(RateLimitMiddleware.cleanup_loop(stop_event))
    runtime_janitor_task = asyncio.create_task(
        run_session_runtime_janitor(stop_event=stop_event, db_factory=AsyncSessionLocal)
    )
    scheduler_task: asyncio.Task | None = None
    embedding_backfill_task: asyncio.Task | None = None
    await init_db()

    # Seed default auth credentials on first run (no-op if already set).
    async with AsyncSessionLocal() as _auth_db:
        from app.services.auth_service import ensure_default_auth_settings
        try:
            await ensure_default_auth_settings(_auth_db)
        except RuntimeError:
            pass  # No env credentials configured; credentials must be set via CLI or reset-auth.

    # Load persisted API keys from DB — env vars take precedence
    async with AsyncSessionLocal() as _db:
        from sqlalchemy import select as _sel
        from app.models.system import SystemSetting as _SS

        # Keys that should only load from DB when the env var is empty/None
        _key_map = {
            "anthropic_api_key": "anthropic_api_key",
            "anthropic_oauth_token": "anthropic_oauth_token",
            "openai_api_key": "openai_api_key",
            "openai_oauth_token": "openai_oauth_token",
            "gemini_api_key": "gemini_api_key",
            "gemini_oauth_credentials": "gemini_oauth_credentials",
            "default_system_prompt": "default_system_prompt",
            "telegram_bot_token": "telegram_bot_token",
            "telegram_owner_user_id": "telegram_owner_user_id",
            "telegram_owner_chat_id": "telegram_owner_chat_id",
            "telegram_owner_telegram_user_id": "telegram_owner_telegram_user_id",
            "telegram_pairing_code_hash": "telegram_pairing_code_hash",
            "telegram_pairing_code_expires_at": "telegram_pairing_code_expires_at",
        }
        # Keys that should ALWAYS load from DB (DB overrides defaults)
        _always_load = {
            "primary_provider": "primary_provider",
        }
        for _db_key, _settings_attr in _key_map.items():
            if not getattr(settings, _settings_attr, None):
                _r = await _db.execute(_sel(_SS).where(_SS.key == _db_key))
                _s = _r.scalars().first()
                if _s:
                    setattr(settings, _settings_attr, _s.value)
        for _db_key, _settings_attr in _always_load.items():
            _r = await _db.execute(_sel(_SS).where(_SS.key == _db_key))
            _s = _r.scalars().first()
            if _s:
                setattr(settings, _settings_attr, _s.value)

    # Seed default module permissions.
    async with AsyncSessionLocal() as _araios_db:
        from app.models.araios import AraiosPermission
        from app.services.araios.permissions import combined_agent_permissions

        _existing_result = await _araios_db.execute(_sel(AraiosPermission))
        _existing_actions = {p.action for p in _existing_result.scalars().all()}
        for _action, _level in combined_agent_permissions().items():
            if _action not in _existing_actions:
                _araios_db.add(AraiosPermission(action=_action, level=_level))
        await _araios_db.commit()

    embedding_key = settings.embedding_api_key or settings.openai_api_key
    embedding_service = None
    if embedding_key:
        embedding_service = EmbeddingService(
            embedding_key,
            model=settings.embedding_model,
            base_url=settings.embedding_base_url,
        )
        if settings.memory_embedding_backfill_on_start:
            embedding_backfill_task = asyncio.create_task(
                run_memory_embedding_backfill(
                    stop_event=stop_event,
                    db_factory=AsyncSessionLocal,
                    embedding_service=embedding_service,
                    batch_size=settings.memory_embedding_backfill_batch_size,
                    max_rows=settings.memory_embedding_backfill_max_rows,
                )
            )
    memory_search_service = MemorySearchService(embedding_service)
    browser_pool = BrowserPool()
    configure_runtime_services(
        embedding_service=embedding_service,
        memory_search_service=memory_search_service,
        browser_pool=browser_pool,
        app_state=app.state,
    )

    # Recover running runtime containers that survived a backend restart/reload
    try:
        from app.services.runtime import get_runtime
        _rt = get_runtime()
        if hasattr(_rt, "recover_existing"):
            _recovered = await _rt.recover_existing()
            if _recovered:
                logger.info("Recovered %d existing runtime container(s)", _recovered)
    except Exception:
        logger.debug("Runtime container recovery skipped", exc_info=True)

    registry = await build_runtime_registry(session_factory=AsyncSessionLocal)
    executor = ToolExecutor(
        registry,
        approval_waiter=build_tool_db_approval_waiter(session_factory=AsyncSessionLocal),
        approval_result_recorder=build_tool_db_approval_result_recorder(session_factory=AsyncSessionLocal),
    )

    available_tools = {tool.name for tool in registry.list_all()}
    ws_manager = ConnectionManager()
    run_registry = AgentRunRegistry()
    # Let the TerminalManager broadcast terminal_opened/closed/busy events to
    # any subscribers of a chat session's WS stream.
    configure_terminal_event_broadcaster(ws_manager.broadcast)
    wakeup_pending: dict[str, deque[str]] = defaultdict(deque)
    wakeup_workers: set[str] = set()
    wakeup_lock = asyncio.Lock()
    # Live handles for every spawned drainer. Tracking them lets the lifespan
    # finally cancel any in-flight wakeup loop deterministically instead of
    # letting uvicorn wait on orphaned tasks. Entries self-evict.
    wakeup_drainer_tasks: set[asyncio.Task[None]] = set()

    def _spawn_wakeup_drainer(session_id: object) -> None:
        task = asyncio.create_task(_drain_main_agent_wakeups(session_id))
        wakeup_drainer_tasks.add(task)
        task.add_done_callback(wakeup_drainer_tasks.discard)

    provider = build_tier_provider_from_settings(settings)
    app.state.tool_registry = registry
    app.state.tool_executor = executor
    app.state.db_session_factory = AsyncSessionLocal
    app.state.approval_service = ApprovalService(session_factory=AsyncSessionLocal)
    app.state.embedding_service = embedding_service
    app.state.memory_search_service = memory_search_service
    app.state.browser_pool = browser_pool
    app.state.ws_manager = ws_manager
    app.state.agent_run_registry = run_registry
    app.state.llm_provider = provider
    app.state.agent_runtime_support = None

    async def _wakeup_main_agent(session_id: object, prompt: str) -> bool:
        """Server-initiated agent turn triggered by queued background updates.

        Returns True when one queued wakeup item is consumed, False when it
        should be retried later (for example while another run is active).
        """
        from uuid import UUID as _UUID

        from sqlalchemy import select as _select

        from app.models import Session as SessionModel

        agent_runtime_support = app.state.agent_runtime_support
        if agent_runtime_support is None:
            return True

        session_key = str(session_id)

        if await run_registry.is_running(session_key):
            return False

        async with AsyncSessionLocal() as db:
            sid = session_id if isinstance(session_id, _UUID) else _UUID(str(session_id))
            result = await db.execute(_select(SessionModel).where(SessionModel.id == sid))
            session = result.scalars().first()
            if session is None:
                return True

            await ws_manager.broadcast_agent_thinking(session_key)

            async def _on_event(event) -> None:
                await ws_manager.broadcast_agent_event(
                    session_key,
                    runtime_event_to_sentinel_event(event),
                )

            runtime = SentinelLoopRuntimeAdapter(loop=agent_runtime_support, db=db, session_id=sid)
            run_task = asyncio.create_task(
                runtime.run_turn(
                    RunTurnRequest(
                        conversation_id=session_key,
                        new_items=[
                            ConversationItem(
                                id=f"server-wakeup-{uuid4().hex}",
                                role="user",
                                content=[
                                    TextBlock(
                                        text=prompt
                                    )
                                ],
                            )
                        ],
                        config=GenerationConfig(
                            model=TierName.NORMAL.value,
                            max_iterations=10,
                            stream=True,
                            provider_metadata={"persist_user_message": False},
                        ),
                        interjection_source=lambda: run_registry.drain_interjections(session_key),
                    ),
                    sink=_on_event,
                )
            )
            registered = await run_registry.register(session_key, run_task)
            if not registered:
                run_task.cancel()
                return False

            try:
                await run_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001
                await ws_manager.broadcast_agent_error(session_key, str(exc))
                await ws_manager.broadcast_done(session_key, "error")
            finally:
                await run_registry.clear(session_key, run_task)
                try:
                    from app.services.sessions.compaction import CompactionService

                    await CompactionService(provider=agent_runtime_support.provider).auto_compact_if_needed(
                        db, session_id=sid
                    )
                except Exception:  # noqa: BLE001
                    pass
                try:
                    await SessionNamingService(
                        provider=agent_runtime_support.provider,
                        ws_manager=ws_manager,
                    ).maybe_auto_rename(session_id=sid)
                except Exception:  # noqa: BLE001
                    pass
        return True

    async def _enqueue_main_agent_wakeup(session_id: object, prompt: str) -> None:
        session_key = str(session_id)
        should_start_worker = False
        async with wakeup_lock:
            wakeup_pending[session_key].append(prompt)
            if session_key not in wakeup_workers:
                wakeup_workers.add(session_key)
                should_start_worker = True
        if should_start_worker:
            _spawn_wakeup_drainer(session_id)

    async def _drain_main_agent_wakeups(session_id: object) -> None:
        session_key = str(session_id)
        try:
            while True:
                async with wakeup_lock:
                    pending = wakeup_pending.get(session_key)
                    prompt = pending[0] if pending else None
                if prompt is None:
                    return

                consumed = await _wakeup_main_agent(session_id, prompt)
                if not consumed:
                    await asyncio.sleep(0.75)
                    continue

                async with wakeup_lock:
                    current = wakeup_pending.get(session_key)
                    if not current:
                        wakeup_pending.pop(session_key, None)
                    else:
                        current.popleft()
                    if not current:
                        wakeup_pending.pop(session_key, None)
        finally:
            async with wakeup_lock:
                wakeup_workers.discard(session_key)
                has_pending = bool(wakeup_pending.get(session_key))
                should_restart = has_pending and session_key not in wakeup_workers
                if should_restart:
                    wakeup_workers.add(session_key)
            if should_restart:
                _spawn_wakeup_drainer(session_id)

    def _runtime_job_report_text(
        job: dict[str, object],
        *,
        stdout_tail: str,
        stderr_tail: str,
    ) -> str:
        lines = [
            "[Runtime Job Report]",
            "A background runtime job just finished while you were working.",
            "Finish the current step, then integrate this result on the next loop if relevant. If you are already wrapping up, process it immediately afterward.",
            "",
            f"Job ID: {str(job.get('id') or '').strip()}",
            f"Status: {str(job.get('status') or '').strip() or 'unknown'}",
        ]
        returncode = job.get("returncode")
        if returncode is not None:
            lines.append(f"Return code: {returncode}")
        command = str(job.get("command") or "").strip()
        if command:
            lines.extend(["", "Command:", command])
        stdout_text = stdout_tail.strip()
        if stdout_text:
            lines.extend(["", "Stdout tail:", stdout_text])
        stderr_text = stderr_tail.strip()
        if stderr_text:
            lines.extend(["", "Stderr tail:", stderr_text])
        return "\n".join(lines).strip()

    async def _handle_runtime_job_completed(
        session_id: str,
        job: dict[str, object],
        stdout_tail: str,
        stderr_tail: str,
    ) -> None:
        session_key = str(session_id)
        run_registry.enqueue_interjection(
            session_key,
            ConversationItem(
                id=f"runtime-job-report-{str(job.get('id') or uuid4().hex)}",
                role="system",
                content=[TextBlock(text=_runtime_job_report_text(job, stdout_tail=stdout_tail, stderr_tail=stderr_tail))],
                metadata={
                    "source": "runtime_job_completion",
                    "job_id": str(job.get("id") or ""),
                    "status": str(job.get("status") or ""),
                },
            ),
        )
        if await run_registry.is_running(session_key):
            return
        await _enqueue_main_agent_wakeup(
            session_id,
            (
                "A background runtime job just finished. Review the latest [Runtime Job Report] system message(s), "
                "integrate useful findings, and continue helping the user immediately."
            ),
        )

    async def _resume_pending_runtime_job_updates(session_key: str) -> None:
        await _enqueue_main_agent_wakeup(
            session_key,
            (
                "A background runtime job finished while you were busy. Review the latest [Runtime Job Report] "
                "system message(s), integrate useful findings, and continue helping the user immediately."
            ),
        )

    run_registry.configure_idle_interjections_callback(_resume_pending_runtime_job_updates)
    configure_runtime_job_completion_callback(_handle_runtime_job_completed)
    # Same hook reused for the new tmux-backed background runs: completion of
    # a `runtime.user(background=true)` lands in `_handle_runtime_job_completed`,
    # which queues an interjection + wakeup so the agent gets a fresh turn
    # with the result. There is no separate notification channel for
    # background runs — they share the runtime-job completion path.
    configure_terminal_completion_handler(_handle_runtime_job_completed)

    async def _broadcast_sub_agent_completed(task) -> None:
        await ws_manager.broadcast_sub_agent_completed(
            str(task.session_id),
            str(task.id),
            task.status,
            task.result if isinstance(task.result, dict) else None,
        )
        # Persist a system message in the parent session so the main agent sees the result
        result_data = task.result if isinstance(task.result, dict) else {}
        summary = (
            result_data.get("final_text", "") or f"Sub-agent completed with status: {task.status}"
        )
        content = (
            f"[Sub-Agent Report] Task: {task.objective}\n"
            f"Status: {task.status}\n"
            f"Result: {summary[:2000]}"
        )
        try:
            from app.models import Message as MsgModel

            async with AsyncSessionLocal() as db:
                msg = MsgModel(
                    session_id=task.session_id,
                    role="system",
                    content=content,
                    metadata_json={"source": "sub_agent", "task_id": str(task.id)},
                )
                db.add(msg)
                await db.commit()
        except Exception:  # noqa: BLE001
            pass
        await _enqueue_main_agent_wakeup(
            task.session_id,
            (
                "A delegated sub-agent just finished. Review the latest [Sub-Agent Report] system message(s), "
                "integrate useful findings, and continue helping the user immediately."
            ),
        )

    app.state.sub_agent_orchestrator = SubAgentOrchestrator(
        agent_runtime_support=None,
        db_factory=AsyncSessionLocal,
        base_tool_registry=registry,
        on_task_completed=_broadcast_sub_agent_completed,
    )
    configure_runtime_services(
        sub_agent_orchestrator=app.state.sub_agent_orchestrator,
        ws_manager=ws_manager,
    )
    if provider is not None:
        context_builder = ContextBuilder(
            default_system_prompt=settings.default_system_prompt,
            available_tools=available_tools,
            memory_search_service=memory_search_service,
        )
        app.state.agent_runtime_support = SentinelRuntimeSupport(
            provider, context_builder, registry, executor,
        )
        app.state.sub_agent_orchestrator = SubAgentOrchestrator(
            agent_runtime_support=app.state.agent_runtime_support,
            db_factory=AsyncSessionLocal,
            base_tool_registry=registry,
            on_task_completed=_broadcast_sub_agent_completed,
        )
        configure_runtime_services(sub_agent_orchestrator=app.state.sub_agent_orchestrator)

    app.state.trigger_scheduler = TriggerScheduler(
        agent_runtime_support=app.state.agent_runtime_support,
        tool_executor=executor,
        ws_manager=ws_manager,
        run_registry=app.state.agent_run_registry,
        db_factory=AsyncSessionLocal,
    )
    scheduler_task = asyncio.create_task(app.state.trigger_scheduler.start(stop_event))

    # --- Telegram bridge ---
    telegram_stop_event = asyncio.Event()
    app.state.telegram_bridge = None
    app.state.telegram_stop_event = telegram_stop_event
    app.state.telegram_task = None

    if settings.telegram_bot_token:
        from app.services.telegram import start_telegram_bridge

        await start_telegram_bridge(app.state)

    try:
        yield
    finally:
        # Every await below is bounded. Cooperative tasks finish in well under
        # a second; the caps only matter when something legitimately wedges
        # (playwright.stop hanging on a dead CDP session, an asyncssh poll
        # not honouring cancellation fast enough, etc.). Without bounds, a
        # single stuck await pegs the whole uvicorn --reload cycle.
        async def _bounded(name: str, coro: Awaitable[Any], timeout: float) -> None:
            try:
                await asyncio.wait_for(coro, timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning("shutdown step %r exceeded %.1fs deadline; abandoning", name, timeout)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.warning("shutdown step %r raised", name, exc_info=True)

        configure_runtime_job_completion_callback(None)
        configure_terminal_completion_handler(None)
        run_registry.configure_idle_interjections_callback(None)
        stop_event.set()

        # 1. Cancel any in-flight chat turns first so their streaming work
        #    doesn't hold open downstream resources (provider HTTP, asyncssh).
        cancelled_runs = await run_registry.cancel_all(timeout_seconds=3.0)
        if cancelled_runs:
            logger.info("shutdown: cancelled %d active agent run(s)", cancelled_runs)

        # 2. Stop background terminal watchers so their asyncssh polls die.
        await _bounded("terminal_manager.shutdown",
                       get_terminal_manager().shutdown(timeout=3.0),
                       timeout=4.0)

        # 3. Cancel any pending wakeup drainers — they only do small work but
        #    can be mid-await on a generation that we just cancelled above.
        if wakeup_drainer_tasks:
            for task in list(wakeup_drainer_tasks):
                task.cancel()
            await _bounded(
                "wakeup_drainers",
                asyncio.gather(*list(wakeup_drainer_tasks), return_exceptions=True),
                timeout=2.0,
            )

        # 4. Cooperative loops (all use stop_event); bound just in case.
        await _bounded("rate_limit_cleanup", cleanup_task, timeout=2.0)
        await _bounded("runtime_janitor", runtime_janitor_task, timeout=2.0)
        if embedding_backfill_task is not None:
            await _bounded("embedding_backfill", embedding_backfill_task, timeout=2.0)
        if scheduler_task is not None:
            await _bounded("trigger_scheduler", scheduler_task, timeout=3.0)

        # 5. External resources — these are the historical hang sources.
        from app.services.telegram import stop_telegram_bridge

        await _bounded("telegram_bridge", stop_telegram_bridge(app.state), timeout=3.0)
        await _bounded("browser_pool", browser_pool.close_all(), timeout=5.0)


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
# Runtime singletons initialized up-front for deterministic app.state shape.
app.state.llm_provider = None
app.state.ws_manager = ConnectionManager()
app.state.agent_run_registry = AgentRunRegistry()
app.state.approval_service = ApprovalService(session_factory=AsyncSessionLocal)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
register_error_handlers(app)

app.include_router(health.router, tags=["health"])
app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(sessions.router, prefix="/api/v1/sessions", tags=["sessions"])
app.include_router(sessions_compaction.router, prefix="/api/v1/sessions", tags=["sessions"])
app.include_router(memory.router, prefix="/api/v1/memory", tags=["memory"])
app.include_router(sub_agents.router, prefix="/api/v1/sessions", tags=["sub-agents"])
app.include_router(triggers.router, prefix="/api/v1/triggers", tags=["triggers"])
app.include_router(webhooks.router, prefix="/api/v1/webhooks", tags=["webhooks"])
app.include_router(git_router.router, prefix="/api/v1/git", tags=["git"])
app.include_router(approvals_router.router, prefix="/api/v1/approvals", tags=["approvals"])
app.include_router(admin.router, prefix="/api/v1/admin", tags=["admin"])
app.include_router(models.router, prefix="/api/v1/models", tags=["models"])
app.include_router(agent_modes_router.router, prefix="/api/v1/agent-modes", tags=["agent-modes"])
app.include_router(onboarding.router, prefix="/api/v1/onboarding", tags=["onboarding"])
app.include_router(settings_router.router, prefix="/api/v1/settings", tags=["settings"])
app.include_router(runtime.router, prefix="/api/v1/runtime", tags=["runtime"])
app.include_router(telegram.router, prefix="/api/v1/telegram", tags=["telegram"])
app.include_router(vnc_proxy.router, tags=["vnc"])
app.include_router(ws.router, prefix="/ws/sessions", tags=["ws"])

# Module/control-plane routes used by the Sentinel modules surface.
app.include_router(araios_api_router, prefix="/api", tags=["modules"])
