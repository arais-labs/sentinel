import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

# Configure logging so our debug/info logs are visible
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
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
    auth,
    health,
    memory,
    models,
    onboarding,
    playwright,
    sessions,
    sessions_compaction,
    skills,
    sub_agents,
    telegram,
    tools,
    triggers,
    ws,
    webhooks,
)
from app.services.agent import AgentLoop, ContextBuilder, ToolAdapter
from app.services.agent_run_registry import AgentRunRegistry
from app.services.embeddings import EmbeddingService
from app.services.llm import AnthropicProvider, CodexProvider, GeminiProvider, OpenAIProvider
from app.services.llm.base import LLMProvider
from app.services.llm.tier_provider import TierConfig, TierModelConfig, TierProvider
from app.services.llm.types import ReasoningConfig
from app.services.memory_search import MemorySearchService
from app.services.skills import SkillRegistry, load_builtin_skills
from app.services.sub_agents import SubAgentOrchestrator
from app.services.tools import BrowserManager, ToolExecutor, build_default_registry
from app.services.tools.builtin import (
    check_sub_agent_tool,
    list_sub_agents_tool,
    python_xagent_tool,
    spawn_sub_agent_tool,
)
from app.services.trigger_scheduler import TriggerScheduler
from app.services.ws_manager import ConnectionManager


def _build_llm_provider() -> LLMProvider | None:
    anthropic: LLMProvider | None = None
    openai: LLMProvider | None = None
    gemini: LLMProvider | None = None
    openai_is_codex = False

    anthropic_token = settings.anthropic_oauth_token or settings.anthropic_api_key
    if anthropic_token:
        anthropic = AnthropicProvider(anthropic_token)

    # OAuth token → Codex provider (different endpoint + models)
    # API key → standard OpenAI provider
    if settings.openai_oauth_token:
        openai = CodexProvider(settings.openai_oauth_token)
        openai_is_codex = True
    elif settings.openai_api_key:
        openai = OpenAIProvider(settings.openai_api_key, base_url=settings.openai_base_url)

    if settings.gemini_api_key:
        gemini = GeminiProvider(settings.gemini_api_key)

    if not anthropic and not openai and not gemini:
        return None

    # --- Build per-tier configs ---
    tier_defs: list[tuple[str, str, str, str, str, int, float, int, str, int]] = [
        # (tier_name, anthropic_model, openai_model, codex_model, gemini_model,
        #  max_tokens, temperature, thinking_budget, reasoning_effort, gemini_thinking_budget)
        (
            "fast",
            settings.tier_fast_anthropic_model,
            settings.tier_fast_openai_model,
            settings.tier_fast_codex_model,
            settings.tier_fast_gemini_model,
            settings.tier_fast_max_tokens,
            settings.tier_fast_temperature,
            settings.tier_fast_anthropic_thinking_budget,
            settings.tier_fast_openai_reasoning_effort,
            settings.tier_fast_gemini_thinking_budget,
        ),
        (
            "normal",
            settings.tier_normal_anthropic_model,
            settings.tier_normal_openai_model,
            settings.tier_normal_codex_model,
            settings.tier_normal_gemini_model,
            settings.tier_normal_max_tokens,
            settings.tier_normal_temperature,
            settings.tier_normal_anthropic_thinking_budget,
            settings.tier_normal_openai_reasoning_effort,
            settings.tier_normal_gemini_thinking_budget,
        ),
        (
            "hard",
            settings.tier_hard_anthropic_model,
            settings.tier_hard_openai_model,
            settings.tier_hard_codex_model,
            settings.tier_hard_gemini_model,
            settings.tier_hard_max_tokens,
            settings.tier_hard_temperature,
            settings.tier_hard_anthropic_thinking_budget,
            settings.tier_hard_openai_reasoning_effort,
            settings.tier_hard_gemini_thinking_budget,
        ),
    ]

    tiers: dict[str, TierConfig] = {}
    for (
        tier_name,
        anth_model,
        oai_model,
        codex_model,
        gem_model,
        max_tok,
        temp,
        thinking_budget,
        reasoning_effort,
        gem_thinking_budget,
    ) in tier_defs:
        anth_cfg = None
        oai_cfg = None
        gem_cfg = None
        if anthropic:
            anth_cfg = TierModelConfig(
                provider=anthropic,
                model=anth_model,
                reasoning_config=ReasoningConfig(
                    max_tokens=max_tok,
                    thinking_budget=thinking_budget if thinking_budget > 0 else None,
                ),
                temperature=temp,
            )
        if openai:
            oai_cfg = TierModelConfig(
                provider=openai,
                model=codex_model if openai_is_codex else oai_model,
                reasoning_config=ReasoningConfig(
                    max_tokens=max_tok,
                    reasoning_effort=reasoning_effort or None,
                ),
                temperature=temp,
            )
        if gemini:
            gem_cfg = TierModelConfig(
                provider=gemini,
                model=gem_model,
                reasoning_config=ReasoningConfig(
                    max_tokens=max_tok,
                    thinking_budget=gem_thinking_budget if gem_thinking_budget > 0 else None,
                ),
                temperature=temp,
            )

        # Collect all available configs, then pick primary + fallback
        all_cfgs: dict[str, TierModelConfig] = {}
        if anth_cfg:
            all_cfgs["anthropic"] = anth_cfg
        if oai_cfg:
            all_cfgs["openai"] = oai_cfg
        if gem_cfg:
            all_cfgs["gemini"] = gem_cfg

        if not all_cfgs:
            continue

        primary_name = settings.primary_provider
        if primary_name in all_cfgs:
            primary = all_cfgs[primary_name]
            fallbacks = [c for name, c in all_cfgs.items() if name != primary_name]
            tiers[tier_name] = TierConfig(primary=primary, fallbacks=fallbacks)
        else:
            cfgs_list = list(all_cfgs.values())
            tiers[tier_name] = TierConfig(primary=cfgs_list[0], fallbacks=cfgs_list[1:])

    return TierProvider(tiers=tiers, default_tier="normal", max_retries=settings.llm_max_retries)


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop_event = asyncio.Event()
    cleanup_task = asyncio.create_task(RateLimitMiddleware.cleanup_loop(stop_event))
    scheduler_task: asyncio.Task | None = None
    await init_db()

    # Load persisted API keys from DB (set during onboarding) — env vars take precedence
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
            "telegram_target_session_id": "telegram_target_session_id",
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

    embedding_key = settings.embedding_api_key or settings.openai_api_key
    embedding_service = None
    if embedding_key:
        embedding_service = EmbeddingService(
            embedding_key,
            model=settings.embedding_model,
            base_url=settings.embedding_base_url,
        )
    memory_search_service = MemorySearchService(embedding_service)
    browser_manager = BrowserManager()

    registry = build_default_registry(
        memory_search_service=memory_search_service,
        embedding_service=embedding_service,
        session_factory=AsyncSessionLocal,
        browser_manager=browser_manager,
    )
    executor = ToolExecutor(registry)

    skill_registry = SkillRegistry()
    builtin_dir = Path(__file__).resolve().parent / "services" / "skills" / "builtin"
    for skill in load_builtin_skills(builtin_dir):
        skill_registry.register(skill)

    available_tools = {tool.name for tool in registry.list_all()}
    ws_manager = ConnectionManager()
    run_registry = AgentRunRegistry()

    provider = _build_llm_provider()
    app.state.tool_registry = registry
    app.state.tool_executor = executor
    app.state.skill_registry = skill_registry
    app.state.embedding_service = embedding_service
    app.state.memory_search_service = memory_search_service
    app.state.browser_manager = browser_manager
    app.state.ws_manager = ws_manager
    app.state.agent_run_registry = run_registry
    app.state.llm_provider = provider
    app.state.agent_loop = None

    async def _wakeup_main_agent(session_id: object) -> None:
        """Server-initiated agent turn triggered when all sub-agents complete."""
        from uuid import UUID as _UUID

        from sqlalchemy import select as _select

        from app.models import Session as SessionModel
        from app.services.llm.types import AgentEvent as _AgentEvent

        agent_loop = app.state.agent_loop
        if agent_loop is None:
            return

        session_key = str(session_id)

        if await run_registry.is_running(session_key):
            return

        async with AsyncSessionLocal() as db:
            sid = session_id if isinstance(session_id, _UUID) else _UUID(str(session_id))
            result = await db.execute(_select(SessionModel).where(SessionModel.id == sid))
            session = result.scalars().first()
            if session is None or session.status != "active":
                return

            await ws_manager.broadcast_agent_thinking(session_key)

            async def _on_event(event: _AgentEvent) -> None:
                await ws_manager.broadcast_agent_event(session_key, event)

            run_task = asyncio.create_task(
                agent_loop.run(
                    db,
                    sid,
                    "All delegated sub-agent tasks are now complete. "
                    "Review the sub-agent reports in your context and provide a comprehensive synthesized response to the user.",
                    persist_user_message=False,
                    on_event=_on_event,
                    model="hint:reasoning",
                    max_iterations=10,
                )
            )
            registered = await run_registry.register(session_key, run_task)
            if not registered:
                run_task.cancel()
                return

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
            from app.models import Message as MsgModel, SubAgentTask as SubAgentTaskModel
            from sqlalchemy import select as _select

            async with AsyncSessionLocal() as db:
                msg = MsgModel(
                    session_id=task.session_id,
                    role="system",
                    content=content,
                    metadata_json={"source": "sub_agent", "task_id": str(task.id)},
                )
                db.add(msg)
                await db.commit()

                # Check if all sub-agents for this session are done
                remaining_result = await db.execute(
                    _select(SubAgentTaskModel).where(
                        SubAgentTaskModel.session_id == task.session_id,
                        SubAgentTaskModel.status.in_(["pending", "running"]),
                    )
                )
                remaining = remaining_result.scalars().all()
                if not remaining:
                    asyncio.create_task(_wakeup_main_agent(task.session_id))
        except Exception:  # noqa: BLE001
            pass

    app.state.sub_agent_orchestrator = SubAgentOrchestrator(
        agent_loop=None,
        db_factory=AsyncSessionLocal,
        base_tool_registry=registry,
        on_task_completed=_broadcast_sub_agent_completed,
    )
    if provider is not None:
        context_builder = ContextBuilder(
            default_system_prompt=settings.default_system_prompt,
            skill_registry=skill_registry,
            available_tools=available_tools,
            memory_search_service=memory_search_service,
        )
        tool_adapter = ToolAdapter(registry, executor)
        app.state.agent_loop = AgentLoop(provider, context_builder, tool_adapter)
        app.state.sub_agent_orchestrator = SubAgentOrchestrator(
            agent_loop=app.state.agent_loop,
            db_factory=AsyncSessionLocal,
            base_tool_registry=registry,
            on_task_completed=_broadcast_sub_agent_completed,
        )

        # Register sub-agent management tools (use app.state for lazy orchestrator resolution)
        registry.register(
            spawn_sub_agent_tool(
                session_factory=AsyncSessionLocal,
                orchestrator=app.state.sub_agent_orchestrator,
            )
        )
        registry.register(check_sub_agent_tool(session_factory=AsyncSessionLocal))
        registry.register(list_sub_agents_tool(session_factory=AsyncSessionLocal))
        registry.register(
            python_xagent_tool(
                session_factory=AsyncSessionLocal,
                orchestrator=app.state.sub_agent_orchestrator,
            )
        )
        available_tools.update(
            {
                "spawn_sub_agent",
                "check_sub_agent",
                "list_sub_agents",
                "pythonXagent",
            }
        )
        # Rebuild executor and tool adapter with new tools
        executor = ToolExecutor(registry)
        app.state.tool_executor = executor
        tool_adapter = ToolAdapter(registry, executor)
        app.state.agent_loop.tool_adapter = tool_adapter
        context_builder._available_tools = available_tools

    app.state.trigger_scheduler = TriggerScheduler(
        agent_loop=app.state.agent_loop,
        tool_executor=executor,
        ws_manager=ws_manager,
        db_factory=AsyncSessionLocal,
    )
    scheduler_task = asyncio.create_task(app.state.trigger_scheduler.start(stop_event))

    # --- Telegram bridge ---
    telegram_stop_event = asyncio.Event()
    app.state.telegram_bridge = None
    app.state.telegram_stop_event = telegram_stop_event
    app.state.telegram_task = None

    if settings.telegram_bot_token:
        from app.services.telegram_bridge import start_telegram_bridge

        await start_telegram_bridge(app.state)

    # Register Telegram tools (always available — return errors when bridge is not running/configured)
    from app.services.telegram_bridge import (
        send_telegram_message_tool,
        telegram_manage_integration_tool,
    )

    tg_tool = send_telegram_message_tool(app.state)
    registry.register(tg_tool)
    available_tools.add("send_telegram_message")
    tg_manage_tool = telegram_manage_integration_tool(app.state)
    registry.register(tg_manage_tool)
    available_tools.add("telegram_manage_integration")

    try:
        yield
    finally:
        stop_event.set()
        await cleanup_task
        if scheduler_task is not None:
            await scheduler_task
        from app.services.telegram_bridge import stop_telegram_bridge

        await stop_telegram_bridge(app.state)
        await browser_manager.close()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
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
app.include_router(skills.router, prefix="/api/v1/skills", tags=["skills"])
app.include_router(admin.router, prefix="/api/v1/admin", tags=["admin"])
app.include_router(models.router, prefix="/api/v1/models", tags=["models"])
app.include_router(onboarding.router, prefix="/api/v1/onboarding", tags=["onboarding"])
app.include_router(playwright.router, prefix="/api/v1/playwright", tags=["playwright"])
app.include_router(telegram.router, prefix="/api/v1/telegram", tags=["telegram"])
app.include_router(ws.router, prefix="/ws/sessions", tags=["ws"])
