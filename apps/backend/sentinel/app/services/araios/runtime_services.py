from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.browser.pool import BrowserPool
    from app.services.embeddings import EmbeddingService
    from app.services.memory.search import MemorySearchService
    from app.services.sub_agents import SubAgentOrchestrator
    from app.services.ws.ws_manager import ConnectionManager


_embedding_service: EmbeddingService | None = None
_memory_search_service: MemorySearchService | None = None
_browser_pool: BrowserPool | None = None
_app_state: object | None = None
_sub_agent_orchestrator: SubAgentOrchestrator | None = None
_ws_manager: ConnectionManager | None = None


def configure_runtime_services(
    *,
    embedding_service: EmbeddingService | None = None,
    memory_search_service: MemorySearchService | None = None,
    browser_pool: BrowserPool | None = None,
    app_state: object | None = None,
    sub_agent_orchestrator: SubAgentOrchestrator | None = None,
    ws_manager: ConnectionManager | None = None,
) -> None:
    global _embedding_service, _memory_search_service, _browser_pool
    global _app_state, _sub_agent_orchestrator, _ws_manager

    if embedding_service is not None:
        _embedding_service = embedding_service
    if memory_search_service is not None:
        _memory_search_service = memory_search_service
    if browser_pool is not None:
        _browser_pool = browser_pool
    if app_state is not None:
        _app_state = app_state
    if sub_agent_orchestrator is not None:
        _sub_agent_orchestrator = sub_agent_orchestrator
    if ws_manager is not None:
        _ws_manager = ws_manager


def reset_runtime_services() -> None:
    global _embedding_service, _memory_search_service, _browser_pool
    global _app_state, _sub_agent_orchestrator, _ws_manager

    _embedding_service = None
    _memory_search_service = None
    _browser_pool = None
    _app_state = None
    _sub_agent_orchestrator = None
    _ws_manager = None


def get_embedding_service() -> EmbeddingService | None:
    return _embedding_service


def get_memory_search_service() -> MemorySearchService | None:
    return _memory_search_service


def get_browser_pool() -> BrowserPool:
    global _browser_pool
    if _browser_pool is None:
        from app.services.browser.pool import BrowserPool

        _browser_pool = BrowserPool()
    return _browser_pool


def get_app_state() -> object | None:
    return _app_state


def get_sub_agent_orchestrator() -> SubAgentOrchestrator | None:
    return _sub_agent_orchestrator


def get_ws_manager() -> ConnectionManager | None:
    return _ws_manager
