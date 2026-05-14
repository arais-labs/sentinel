import asyncio
import logging
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import FastAPI

from app.logging_context import configure_logging

# Configure logging so our debug/info logs are visible and session-scoped.
configure_logging()
logger = logging.getLogger(__name__)
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.sentral import ConversationItem, GenerationConfig, RunTurnRequest, TextBlock
from app.config import settings
from app.database import AsyncSessionLocal, ensure_database_exists, init_db, init_instance_db
from app.database.instance_sessions import instance_session_registry
from app.middleware import (
    RateLimitMiddleware,
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
    register_error_handlers,
)
from app.routers import (
    admin,
    admin_manager,
    agent_modes as agent_modes_router,
    approvals as approvals_router,
    auth,
    git as git_router,
    health,
    instances,
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
from app.services.agent_runtime_adapters import SentinelLoopRuntimeAdapter, runtime_event_to_sentinel_event
from app.services.sessions.agent_run_registry import AgentRunRegistry
from app.services.araios.runtime_services import configure_runtime_services
from app.services.memory.embeddings import EmbeddingService
from app.services.llm.factory import build_tier_provider_from_settings
from app.services.llm.ids import TierName
from app.services.memory.search import MemorySearchService
from app.services.runtime.terminal_manager import (
    configure_terminal_completion_handler,
    configure_terminal_event_broadcaster,
    get_terminal_manager,
)
from app.services.runtime.session_runtime import (
    configure_runtime_job_completion_callback,
)
from app.services.sessions.session_naming import SessionNamingService
from app.services.tools.approval import ApprovalService
from app.models.manager import SentinelInstance
from app.services.instance_runtime_context import (
    instance_runtime_context_registry,
)
from app.services.browser.pool import BrowserPool
from app.services.ws.ws_manager import ConnectionManager


async def shutdown_runtime_provider(app_state: Any, bounded: Callable[..., Awaitable[None]]) -> None:
    runtime_provider = getattr(app_state, "runtime_provider", None)
    if runtime_provider is None:
        return
    if hasattr(runtime_provider, "stop_all"):
        await bounded("runtime_provider.stop_all", runtime_provider.stop_all(), timeout=10.0)
    if hasattr(runtime_provider, "cancel_background_prepare"):
        await bounded("runtime_prepare", runtime_provider.cancel_background_prepare(), timeout=5.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop_event = asyncio.Event()
    cleanup_task = asyncio.create_task(RateLimitMiddleware.cleanup_loop(stop_event))
    await init_db()
    async with AsyncSessionLocal() as _auth_db:
        from app.services.auth_service import ensure_default_auth_settings

        await ensure_default_auth_settings(_auth_db)

    # Instance app settings such as provider credentials are loaded from each
    # instance database when that instance runtime context is built.

    embedding_key = settings.embedding_api_key or settings.openai_api_key
    embedding_service = None
    if embedding_key:
        embedding_service = EmbeddingService(
            embedding_key,
            model=settings.embedding_model,
            base_url=settings.embedding_base_url,
        )
    memory_search_service = MemorySearchService(embedding_service)
    browser_pool = BrowserPool()
    ws_manager = ConnectionManager()
    run_registry = AgentRunRegistry()
    app.state.instance_stop_event = stop_event
    app.state.instance_runtime_context_registry = instance_runtime_context_registry
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
        app.state.runtime_provider = _rt
        if hasattr(_rt, "recover_existing"):
            _recovered = await _rt.recover_existing()
            if _recovered:
                logger.info("Recovered %d existing runtime container(s)", _recovered)
        if hasattr(_rt, "start_background_prepare"):
            _rt.start_background_prepare()
    except Exception:
        logger.debug("Runtime container recovery skipped", exc_info=True)

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
    app.state.approval_service = ApprovalService()
    app.state.embedding_service = embedding_service
    app.state.memory_search_service = memory_search_service
    app.state.browser_pool = browser_pool
    app.state.ws_manager = ws_manager
    app.state.agent_run_registry = run_registry
    app.state.llm_provider = provider
    app.state.agent_runtime_support = None

    async def _resolve_runtime_context_for_session(session_id: object):
        from uuid import UUID as _UUID

        from sqlalchemy import select as _select

        from app.models import Session as SessionModel

        try:
            sid = session_id if isinstance(session_id, _UUID) else _UUID(str(session_id))
        except (TypeError, ValueError):
            return None, None
        for context in instance_runtime_context_registry.all():
            async with context.session_factory() as db:
                result = await db.execute(_select(SessionModel.id).where(SessionModel.id == sid))
                if result.scalar_one_or_none() is not None:
                    return context, sid
        return None, sid

    async def _wakeup_main_agent(session_id: object, prompt: str) -> bool:
        """Server-initiated agent turn triggered by queued background updates.

        Returns True when one queued wakeup item is consumed, False when it
        should be retried later (for example while another run is active).
        """
        from sqlalchemy import select as _select

        from app.models import Session as SessionModel

        instance_context, sid = await _resolve_runtime_context_for_session(session_id)
        if instance_context is None or sid is None:
            return True
        agent_runtime_support = instance_context.agent_runtime_support
        if agent_runtime_support is None:
            return True

        session_key = str(session_id)

        if await run_registry.is_running(session_key):
            return False

        async with instance_context.session_factory() as db:
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
                        db_factory=instance_context.session_factory,
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

            instance_context, _sid = await _resolve_runtime_context_for_session(task.session_id)
            if instance_context is None:
                return
            async with instance_context.session_factory() as db:
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

    app.state.sub_agent_completed_callback = _broadcast_sub_agent_completed
    configure_runtime_services(ws_manager=ws_manager)

    async with AsyncSessionLocal() as manager_db:
        result = await manager_db.execute(select(SentinelInstance).order_by(SentinelInstance.name))
        for instance in result.scalars().all():
            try:
                await ensure_database_exists(instance.database_name)
                await init_instance_db(instance.database_name)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "instance %s: failed to provision DB %s at startup; skipping",
                    instance.name,
                    instance.database_name,
                    exc_info=True,
                )
                continue
            session_factory = instance_session_registry.session_factory(instance.database_name)
            try:
                await instance_runtime_context_registry.get_or_create(
                    app_state=app.state,
                    instance=instance,
                    session_factory=session_factory,
                )
            except Exception:  # noqa: BLE001
                logger.error(
                    "instance %s: runtime context build failed at startup; skipping",
                    instance.name,
                    exc_info=True,
                )

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

        # 4. Runtime providers own external execution resources. Stop them
        #    while the backend is still alive so detached VMs/containers do not
        #    survive desktop quit or server shutdown.
        await shutdown_runtime_provider(app.state, _bounded)

        # 5. Cooperative loops (all use stop_event); bound just in case.
        await _bounded("rate_limit_cleanup", cleanup_task, timeout=2.0)
        await _bounded("instance_contexts", instance_runtime_context_registry.stop_all(), timeout=5.0)

        # 6. External resources — these are the historical hang sources.
        from app.services.telegram import stop_telegram_bridge

        await _bounded("telegram_bridge", stop_telegram_bridge(app.state), timeout=3.0)
        await _bounded("browser_pool", browser_pool.close_all(), timeout=5.0)
        await _bounded("instance_db_engines", instance_session_registry.dispose_all(), timeout=3.0)


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
# Runtime singletons initialized up-front for deterministic app.state shape.
app.state.llm_provider = None
app.state.ws_manager = ConnectionManager()
app.state.agent_run_registry = AgentRunRegistry()
app.state.approval_service = ApprovalService()
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
app.include_router(instances.router, prefix="/api/v1/instances", tags=["instances"])
app.include_router(admin_manager.router, prefix="/api/v1/admin", tags=["admin"])
_instance_api_prefix = "/api/v1/instances/{instance_name}"
app.include_router(sessions.router, prefix=f"{_instance_api_prefix}/sessions", tags=["sessions"])
app.include_router(sessions_compaction.router, prefix=f"{_instance_api_prefix}/sessions", tags=["sessions"])
app.include_router(memory.router, prefix=f"{_instance_api_prefix}/memory", tags=["memory"])
app.include_router(sub_agents.router, prefix=f"{_instance_api_prefix}/sessions", tags=["sub-agents"])
app.include_router(triggers.router, prefix=f"{_instance_api_prefix}/triggers", tags=["triggers"])
app.include_router(webhooks.router, prefix=f"{_instance_api_prefix}/webhooks", tags=["webhooks"])
app.include_router(git_router.router, prefix=f"{_instance_api_prefix}/git", tags=["git"])
app.include_router(approvals_router.router, prefix=f"{_instance_api_prefix}/approvals", tags=["approvals"])
app.include_router(admin.router, prefix=f"{_instance_api_prefix}/admin", tags=["admin"])
app.include_router(models.router, prefix=f"{_instance_api_prefix}/models", tags=["models"])
app.include_router(agent_modes_router.router, prefix=f"{_instance_api_prefix}/agent-modes", tags=["agent-modes"])
app.include_router(onboarding.router, prefix=f"{_instance_api_prefix}/onboarding", tags=["onboarding"])
app.include_router(settings_router.router, prefix=f"{_instance_api_prefix}/settings", tags=["settings"])
app.include_router(runtime.router, prefix=f"{_instance_api_prefix}/runtime", tags=["runtime"])
app.include_router(telegram.router, prefix=f"{_instance_api_prefix}/telegram", tags=["telegram"])
app.include_router(vnc_proxy.router, tags=["vnc"])
app.include_router(ws.router, prefix="/ws/instances/{instance_name}/sessions", tags=["ws"])

# Module/control-plane routes used by the Sentinel modules surface.
app.include_router(araios_api_router, prefix=_instance_api_prefix, tags=["modules"])
