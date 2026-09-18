import asyncio

from app.services.agent.agent_modes import effective_agent_mode
from app.services.host_runtime import host_processes
from sentral.llm.http_pool import close_provider_http_pool
from sentral.llm.providers.codex import close_codex_connections
from app.services.runtime.ssh_runtime import close_runtime_terminal_manager
from app.services.runtime.remote_mac import close_all as close_remote_runtimes
from app.services.sessions.compaction import CompactionService

import logging
import os
from collections import defaultdict, deque
from collections.abc import Awaitable
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID as _UUID
from uuid import uuid4

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy import select as _select

from app.config import app_version, settings
from app.database import AsyncSessionLocal
from app.database.initialization import init_db, init_instance_db
from app.database.instance_sessions import instance_session_registry
from app.logging_context import configure_logging
from app.middleware import (
    RateLimitMiddleware,
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
    register_error_handlers,
)
from app.middleware.desktop import DesktopTransportMiddleware
from app.models import Session as SessionModel
from app.models.manager import SentinelInstance
from app.routers import (
    admin,
    backup,
    health,
    instances,
    machines,
    memory,
    models,
    module_permissions,
    onboarding,
    runtime,
    sessions,
    sessions_compaction,
    sub_agents,
    telegram,
    triggers,
    voice,
    webhooks,
    workspaces,
    ws,
)
from app.routers import (
    agent_modes as agent_modes_router,
)
from app.routers import (
    approvals as approvals_router,
)
from app.routers import (
    git as git_router,
)
from app.routers import mcp as mcp_router
from app.routers import modules as modules_router
from app.routers import (
    settings as settings_router,
    ollama as ollama_router,
)
from app.routers import (
    version as version_router,
)
from sentral import ConversationItem, GenerationConfig, RunTurnRequest, TextBlock
import app.services.agent_runtime_adapters.runtime as runtime_adapter_module
from sentral.llm.runtime_conversions import runtime_event_to_sentinel_event
from app.services.instance_runtime_context import (
    instance_runtime_context_registry,
)
from app.services.memory.local_embeddings import LocalEmbeddingService
from app.services.voice.runtime import VoiceRuntime
from app.services.memory.search import MemorySearchService
from app.services.modules.runtime_services import configure_runtime_services
from app.services.sessions.agent_run_registry import AgentRunRegistry
from app.services.sessions.session_naming import SessionNamingService
from app.services.sub_agents.accounting import wakeup_model
from app.services.sub_agents.completions import deliver_completion
from app.services.tools.approval import ApprovalService
from app.services.ws.ws_manager import ConnectionManager

# Configure logging so our debug/info logs are visible and session-scoped.
configure_logging()
logger = logging.getLogger(__name__)

_LLM_CREDENTIAL_ENV_VARS = (
    "ANTHROPIC_OAUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "OPENAI_OAUTH_TOKEN",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GEMINI_OAUTH_CREDENTIALS",
    "EMBEDDING_API_KEY",
)


def _warn_if_llm_creds_in_env() -> None:
    """Surface a clear warning if anyone still sets legacy LLM cred env vars.

    These were removed as a supported configuration source; credentials live
    in the per-instance system_settings DB now.
    """

    leaked = [name for name in _LLM_CREDENTIAL_ENV_VARS if os.environ.get(name)]
    if leaked:
        logger.warning(
            "Ignoring LLM credential env vars %s — env-based LLM credentials are no "
            "longer supported. Configure credentials via the in-app Settings page; "
            "they are persisted to the instance's system_settings table.",
            leaked,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _warn_if_llm_creds_in_env()
    stop_event = asyncio.Event()
    cleanup_task = asyncio.create_task(RateLimitMiddleware.cleanup_loop(stop_event))
    await init_db()
    # Instance app settings such as provider credentials are loaded from each
    # instance database when that instance runtime context is built.

    embedding_service = LocalEmbeddingService(settings.storage_root / "models" / "embeddings")
    memory_search_service = MemorySearchService(embedding_service)
    ws_manager = ConnectionManager()
    run_registry = AgentRunRegistry()
    app.state.instance_stop_event = stop_event
    app.state.instance_runtime_context_registry = instance_runtime_context_registry
    configure_runtime_services(
        embedding_service=embedding_service,
        memory_search_service=memory_search_service,
        app_state=app.state,
    )

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

    app.state.approval_service = ApprovalService()
    app.state.embedding_service = embedding_service
    app.state.memory_search_service = memory_search_service
    app.state.ws_manager = ws_manager
    app.state.agent_run_registry = run_registry
    app.state.voice_runtime = VoiceRuntime(settings.storage_root)
    from app.services.llm.ollama_models import OllamaPulls

    app.state.ollama_pulls = OllamaPulls()

    async def _resolve_runtime_context_for_session(session_id: object):

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

        instance_context, sid = await _resolve_runtime_context_for_session(session_id)
        if instance_context is None or sid is None:
            return True
        agent_runtime_support = instance_context.agent_runtime_support
        if agent_runtime_support is None:
            return True

        session_key = str(session_id)

        if await run_registry.is_running(session_key):
            return False

        queued_steering = [
            item
            for item in run_registry.peek_interjections(session_key)
            if item.metadata.get("steering") == "pending"
        ]
        if prompt == "__sentinel_steering__" and not queued_steering:
            return True
        steering_metadata = queued_steering[0].metadata if queued_steering else {}
        steering_generation = steering_metadata.get("generation") or {}

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

            runtime = runtime_adapter_module.SentinelLoopRuntimeAdapter(
                loop=agent_runtime_support, db=db, session_id=sid
            )
            run_task = await run_registry.start(
                session_key,
                runtime.run_turn(
                    RunTurnRequest(
                        conversation_id=session_key,
                        new_items=(
                            []
                            if queued_steering
                            else [
                                ConversationItem(
                                    id=f"server-wakeup-{uuid4().hex}",
                                    role="user",
                                    content=[TextBlock(text=prompt)],
                                )
                            ]
                        ),
                        config=GenerationConfig(
                            model=await wakeup_model(db, sid, steering_metadata),
                            max_iterations=(
                                steering_generation.get("max_iterations", 0)
                                if queued_steering
                                else 0
                            ),
                            stream=True,
                            provider_metadata={
                                "persist_user_message": False,
                                "agent_mode": effective_agent_mode(
                                    session.kind, steering_metadata.get("agent_mode")
                                ),
                            },
                        ),
                        interjection_source=lambda: run_registry.drain_interjections(session_key),
                    ),
                    sink=_on_event,
                ),
                require_interjections=bool(queued_steering),
            )
            if run_task is None:
                return False

            try:
                await ws_manager.broadcast(session_key, {"type": "run_state", "run_active": True})
                await run_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001
                await ws_manager.broadcast_agent_error(session_key, str(exc))
                await ws_manager.broadcast_done(session_key, "error")
            finally:
                await run_registry.clear(session_key, run_task)
                await ws_manager.broadcast(
                    session_key,
                    {
                        "type": "run_state",
                        "run_active": await run_registry.is_running(session_key),
                    },
                )
                try:

                    await CompactionService(
                        provider=agent_runtime_support.provider
                    ).auto_compact_if_needed(db, session_id=sid)
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
            "[Machine Job Report]",
            "A background runtime job just finished while you were working.",
            "Finish the current step, then integrate this result on the next loop if relevant. If you are already wrapping up, process it immediately afterward.",
            "",
            f"Job ID: {str(job.get('id') or '').strip()}",
            f"Status: {str(job.get('status') or '').strip() or 'unknown'}",
        ]
        if job.get("window_id"):
            lines.append(f"Window: {job['window_id']}")
        if job.get("pane_id"):
            lines.append(f"Pane: {job['pane_id']}")
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
                content=[
                    TextBlock(
                        text=_runtime_job_report_text(
                            job, stdout_tail=stdout_tail, stderr_tail=stderr_tail
                        )
                    )
                ],
                metadata={
                    "source": "runtime_job_completion",
                    "notice": {"title": "Background job report"},
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
                "A background runtime job just finished. Review the latest [Machine Job Report] system message(s), "
                "integrate useful findings, and continue helping the user immediately."
            ),
        )

    async def _resume_pending_runtime_job_updates(session_key: str) -> None:
        if any(
            item.metadata.get("steering") == "pending"
            for item in run_registry.peek_interjections(session_key)
        ):
            await _enqueue_main_agent_wakeup(session_key, "__sentinel_steering__")
            return
        await _enqueue_main_agent_wakeup(
            session_key,
            (
                "A background runtime job finished while you were busy. Review the latest [Machine Job Report] "
                "system message(s), integrate useful findings, and continue helping the user immediately."
            ),
        )

    run_registry.configure_idle_interjections_callback(_resume_pending_runtime_job_updates)

    async def _broadcast_sub_agent_completed(task) -> None:

        instance_context, _sid = await _resolve_runtime_context_for_session(task.session_id)
        if instance_context is None:
            return
        async with instance_context.session_factory() as db:
            delivered = await deliver_completion(db, task, run_registry)
        if delivered:
            await ws_manager.broadcast_sub_agent_completed(
                str(task.session_id),
                str(task.id),
                task.status,
                task.result if isinstance(task.result, dict) else None,
            )

    app.state.sub_agent_completed_callback = _broadcast_sub_agent_completed
    configure_runtime_services(
        ws_manager=ws_manager,
        runtime_job_completed_callback=_handle_runtime_job_completed,
    )

    async with AsyncSessionLocal() as manager_db:
        result = await manager_db.execute(select(SentinelInstance).order_by(SentinelInstance.name))
        for instance in result.scalars().all():
            try:
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
                async with session_factory() as db:
                    await app.state.approval_service.cancel_pending_on_startup(db)
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
                logger.warning(
                    "shutdown step %r exceeded %.1fs deadline; abandoning",
                    name,
                    timeout,
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.warning("shutdown step %r raised", name, exc_info=True)

        run_registry.configure_idle_interjections_callback(None)
        stop_event.set()
        await _bounded("voice_runtime", app.state.voice_runtime.close(), timeout=8.0)
        await _bounded("ollama_pulls", app.state.ollama_pulls.close(), timeout=8.0)

        # 1. Cancel any in-flight chat turns first so their streaming work
        #    doesn't hold open downstream resources (provider HTTP, asyncssh).
        cancelled_runs = await run_registry.cancel_all(timeout_seconds=3.0)
        if cancelled_runs:
            logger.info("shutdown: cancelled %d active agent run(s)", cancelled_runs)

        # 2. Cancel any pending wakeup drainers — they only do small work but
        #    can be mid-await on a generation that we just cancelled above.
        if wakeup_drainer_tasks:
            for task in list(wakeup_drainer_tasks):
                task.cancel()
            await _bounded(
                "wakeup_drainers",
                asyncio.gather(*list(wakeup_drainer_tasks), return_exceptions=True),
                timeout=2.0,
            )

        # 3. Cooperative loops (all use stop_event); bound just in case.
        await _bounded("rate_limit_cleanup", cleanup_task, timeout=2.0)
        await _bounded(
            "instance_contexts",
            instance_runtime_context_registry.stop_all(),
            timeout=5.0,
        )

        await _bounded("host_processes", host_processes.close(), timeout=3.0)

        # 4. External resources.

        await _bounded("codex_connections", close_codex_connections(), timeout=3.0)

        await _bounded("provider_http", close_provider_http_pool(), timeout=3.0)

        await _bounded("runtime_ssh", close_runtime_terminal_manager(), timeout=3.0)

        await _bounded("remote_runtimes", close_remote_runtimes(), timeout=3.0)
        await _bounded("instance_db_engines", instance_session_registry.dispose_all(), timeout=3.0)


app = FastAPI(title=settings.app_name, version=app_version(), lifespan=lifespan)
# Machine singletons initialized up-front for deterministic app.state shape.
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
app.add_middleware(DesktopTransportMiddleware, token=settings.sentinel_desktop_token)
register_error_handlers(app)

app.include_router(health.router, tags=["health"])
app.include_router(version_router.router, tags=["version"])
app.include_router(instances.router, prefix="/api/v1/instances", tags=["instances"])
app.include_router(machines.router, prefix="/api/v1", tags=["machines"])
_instance_api_prefix = "/api/v1/instances/{instance_name}"
app.include_router(workspaces.router, prefix=_instance_api_prefix, tags=["workspaces"])
app.include_router(sessions.router, prefix=f"{_instance_api_prefix}/sessions", tags=["sessions"])
app.include_router(voice.router, prefix=f"{_instance_api_prefix}/voice", tags=["voice"])
app.include_router(
    sessions_compaction.router,
    prefix=f"{_instance_api_prefix}/sessions",
    tags=["sessions"],
)
app.include_router(memory.router, prefix=f"{_instance_api_prefix}/memory", tags=["memory"])
app.include_router(
    sub_agents.router, prefix=f"{_instance_api_prefix}/sessions", tags=["sub-agents"]
)
app.include_router(triggers.router, prefix=f"{_instance_api_prefix}/triggers", tags=["triggers"])
app.include_router(webhooks.router, prefix=f"{_instance_api_prefix}/webhooks", tags=["webhooks"])
app.include_router(git_router.router, prefix=f"{_instance_api_prefix}/git", tags=["git"])
app.include_router(
    approvals_router.router,
    prefix=f"{_instance_api_prefix}/approvals",
    tags=["approvals"],
)
app.include_router(admin.router, prefix=f"{_instance_api_prefix}/admin", tags=["admin"])
app.include_router(models.router, prefix=f"{_instance_api_prefix}/models", tags=["models"])
app.include_router(agent_modes_router.router, prefix="/api/v1/agent-modes", tags=["agent-modes"])
app.include_router(
    onboarding.router, prefix=f"{_instance_api_prefix}/onboarding", tags=["onboarding"]
)
app.include_router(
    settings_router.router, prefix=f"{_instance_api_prefix}/settings", tags=["settings"]
)
app.include_router(
    ollama_router.router,
    prefix=f"{_instance_api_prefix}/settings/ollama",
    tags=["settings"],
)
app.include_router(telegram.router, prefix=f"{_instance_api_prefix}/telegram", tags=["telegram"])
app.include_router(backup.router, prefix=f"{_instance_api_prefix}/backup", tags=["backup"])
app.include_router(runtime.router, prefix=f"{_instance_api_prefix}/runtime", tags=["runtime"])
app.include_router(ws.router, prefix="/ws/instances/{instance_name}/sessions", tags=["ws"])

# Module/control-plane routes used by the Sentinel modules surface.
app.include_router(
    modules_router.router, prefix=f"{_instance_api_prefix}/modules", tags=["modules"]
)
app.include_router(
    module_permissions.router,
    prefix=f"{_instance_api_prefix}/permissions",
    tags=["module-permissions"],
)

app.include_router(mcp_router.router, prefix=f"{_instance_api_prefix}/mcp", tags=["mcp"])
