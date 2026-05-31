from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.browser.pool import BrowserPool
    from app.services.memory.embeddings import EmbeddingService
    from app.services.memory.search import MemorySearchService
    from app.services.sub_agents import SubAgentOrchestrator
    from app.services.ws.ws_manager import ConnectionManager


_browser_pool: BrowserPool | None = None
_embedding_service: EmbeddingService | None = None
_memory_search_service: MemorySearchService | None = None
_app_state: object | None = None
_sub_agent_orchestrator: SubAgentOrchestrator | None = None
_ws_manager: ConnectionManager | None = None
_runtime_job_completed_callback: Callable[..., Awaitable[None]] | None = None


def configure_runtime_services(
    *,
    browser_pool: BrowserPool | None = None,
    embedding_service: EmbeddingService | None = None,
    memory_search_service: MemorySearchService | None = None,
    app_state: object | None = None,
    sub_agent_orchestrator: SubAgentOrchestrator | None = None,
    ws_manager: ConnectionManager | None = None,
    runtime_job_completed_callback: Callable[..., Awaitable[None]] | None = None,
) -> None:
    global _browser_pool
    global _embedding_service, _memory_search_service
    global _app_state, _sub_agent_orchestrator, _ws_manager, _runtime_job_completed_callback

    if browser_pool is not None:
        _browser_pool = browser_pool
    if embedding_service is not None:
        _embedding_service = embedding_service
    if memory_search_service is not None:
        _memory_search_service = memory_search_service
    if app_state is not None:
        _app_state = app_state
    if sub_agent_orchestrator is not None:
        _sub_agent_orchestrator = sub_agent_orchestrator
    if ws_manager is not None:
        _ws_manager = ws_manager
    if runtime_job_completed_callback is not None:
        _runtime_job_completed_callback = runtime_job_completed_callback


def reset_runtime_services() -> None:
    global _browser_pool
    global _embedding_service, _memory_search_service
    global _app_state, _sub_agent_orchestrator, _ws_manager, _runtime_job_completed_callback

    _browser_pool = None
    _embedding_service = None
    _memory_search_service = None
    _app_state = None
    _sub_agent_orchestrator = None
    _ws_manager = None
    _runtime_job_completed_callback = None


def get_browser_pool() -> BrowserPool:
    global _browser_pool
    if _browser_pool is None:
        from app.services.browser.pool import BrowserPool

        _browser_pool = BrowserPool()
    return _browser_pool


def get_embedding_service() -> EmbeddingService | None:
    return _embedding_service


def get_memory_search_service() -> MemorySearchService | None:
    return _memory_search_service


def get_app_state() -> object | None:
    return _app_state


def get_sub_agent_orchestrator() -> SubAgentOrchestrator | None:
    return _sub_agent_orchestrator


def get_ws_manager() -> ConnectionManager | None:
    return _ws_manager


async def notify_runtime_job_completed(
    session_id: str,
    job: dict[str, object],
    *,
    stdout_tail: str,
    stderr_tail: str,
) -> None:
    if _runtime_job_completed_callback is None:
        return
    await _runtime_job_completed_callback(
        session_id,
        job,
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
    )
