import asyncio
import logging
from collections import defaultdict
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.logging_context import configure_logging

# Configure logging so our debug/info logs are visible and session-scoped.
configure_logging()
logger = logging.getLogger(__name__)
# Set DEBUG for our agent/provider modules specifically
logging.getLogger("app.services.agent").setLevel(logging.DEBUG)
logging.getLogger("app.services.llm").setLevel(logging.DEBUG)
from fastapi.middleware.cors import CORSMiddleware

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
    tools,
    triggers,
    vnc_proxy,
    ws,
    webhooks,
)
from app.routers.araios import api_router as araios_api_router, platform_auth_router as araios_platform_auth_router
from app.services.agent import AgentLoop, ContextBuilder, ToolAdapter
from app.services.sessions.agent_run_registry import AgentRunRegistry
from app.services.araios.runtime_services import configure_runtime_services
from app.services.embeddings import EmbeddingService
from app.services.llm.factory import build_tier_provider_from_settings
from app.services.llm.ids import TierName
from app.services.memory.backfill import run_memory_embedding_backfill
from app.services.memory.search import MemorySearchService
from app.services.runtime.session_runtime import run_session_runtime_janitor
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

    # Seed AraiOS default permissions
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
    wakeup_pending: dict[str, int] = defaultdict(int)
    wakeup_workers: set[str] = set()
    wakeup_lock = asyncio.Lock()

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
    app.state.agent_loop = None

    async def _wakeup_main_agent(session_id: object) -> bool:
        """Server-initiated agent turn triggered by sub-agent completion.

        Returns True when one queued wakeup item is consumed, False when it
        should be retried later (for example while another run is active).
        """
        from uuid import UUID as _UUID

        from sqlalchemy import select as _select

        from app.models import Session as SessionModel
        from app.services.llm.generic.types import AgentEvent as _AgentEvent

        agent_loop = app.state.agent_loop
        if agent_loop is None:
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

            async def _on_event(event: _AgentEvent) -> None:
                await ws_manager.broadcast_agent_event(session_key, event)

            run_task = asyncio.create_task(
                agent_loop.run(
                    db,
                    sid,
                    "A delegated sub-agent just finished. "
                    "Review the latest [Sub-Agent Report] system message(s), integrate useful findings, "
                    "and continue helping the user immediately.",
                    persist_user_message=False,
                    on_event=_on_event,
                    model=TierName.NORMAL.value,
                    max_iterations=10,
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
                    from app.services.compaction import CompactionService

                    await CompactionService(provider=agent_loop.provider).auto_compact_if_needed(
                        db, session_id=sid
                    )
                except Exception:  # noqa: BLE001
                    pass
                try:
                    await SessionNamingService(
                        provider=agent_loop.provider,
                        ws_manager=ws_manager,
                    ).maybe_auto_rename(session_id=sid)
                except Exception:  # noqa: BLE001
                    pass
        return True

    async def _enqueue_main_agent_wakeup(session_id: object) -> None:
        session_key = str(session_id)
        should_start_worker = False
        async with wakeup_lock:
            wakeup_pending[session_key] += 1
            if session_key not in wakeup_workers:
                wakeup_workers.add(session_key)
                should_start_worker = True
        if should_start_worker:
            asyncio.create_task(_drain_main_agent_wakeups(session_id))

    async def _drain_main_agent_wakeups(session_id: object) -> None:
        session_key = str(session_id)
        try:
            while True:
                async with wakeup_lock:
                    pending = int(wakeup_pending.get(session_key, 0))
                if pending <= 0:
                    return

                consumed = await _wakeup_main_agent(session_id)
                if not consumed:
                    await asyncio.sleep(0.75)
                    continue

                async with wakeup_lock:
                    current = int(wakeup_pending.get(session_key, 0))
                    if current <= 1:
                        wakeup_pending.pop(session_key, None)
                    else:
                        wakeup_pending[session_key] = current - 1
        finally:
            async with wakeup_lock:
                wakeup_workers.discard(session_key)
                has_pending = int(wakeup_pending.get(session_key, 0)) > 0
                should_restart = has_pending and session_key not in wakeup_workers
                if should_restart:
                    wakeup_workers.add(session_key)
            if should_restart:
                asyncio.create_task(_drain_main_agent_wakeups(session_id))

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
        await _enqueue_main_agent_wakeup(task.session_id)

    app.state.sub_agent_orchestrator = SubAgentOrchestrator(
        agent_loop=None,
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
        tool_adapter = ToolAdapter(registry, executor, session_factory=AsyncSessionLocal)
        app.state.agent_loop = AgentLoop(provider, context_builder, tool_adapter)
        app.state.sub_agent_orchestrator = SubAgentOrchestrator(
            agent_loop=app.state.agent_loop,
            db_factory=AsyncSessionLocal,
            base_tool_registry=registry,
            on_task_completed=_broadcast_sub_agent_completed,
        )
        configure_runtime_services(sub_agent_orchestrator=app.state.sub_agent_orchestrator)

    app.state.trigger_scheduler = TriggerScheduler(
        agent_loop=app.state.agent_loop,
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
        stop_event.set()
        await cleanup_task
        await runtime_janitor_task
        if embedding_backfill_task is not None:
            await embedding_backfill_task
        if scheduler_task is not None:
            await scheduler_task
        from app.services.telegram import stop_telegram_bridge

        await stop_telegram_bridge(app.state)
        await browser_pool.close_all()


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
app.include_router(tools.router, prefix="/api/v1/tools", tags=["tools"])
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

# AraiOS routes — serves /api/* and /platform/auth/* for the AraiOS frontend
app.include_router(araios_api_router, prefix="/api", tags=["araios"])
app.include_router(araios_platform_auth_router, prefix="/platform/auth", tags=["araios-platform-auth"])
