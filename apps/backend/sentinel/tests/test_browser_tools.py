from __future__ import annotations

import asyncio
import base64

from app.services.tools.builtin import build_default_registry
from app.services.tools.browser_tool import BrowserManager
from app.services.tools.executor import ToolExecutor, ToolValidationError


def _run(coro):
    return asyncio.run(coro)


class _StubBrowserManager:
    async def navigate(self, url: str):
        return {"url": url, "title": "Example"}

    async def screenshot(self, *, full_page: bool = True):
        _ = full_page
        return {"image_base64": base64.b64encode(b"fake").decode("ascii")}

    async def click(self, selector: str):
        return {"clicked": True, "selector": selector}

    async def type_text(self, selector: str, text: str):
        return {"typed": True, "selector": selector, "characters": len(text)}

    async def get_text(self, selector: str | None = None):
        return {"selector": selector, "text": "hello"}

    async def get_snapshot(self):
        return {"snapshot": {"role": "WebArea"}}

    async def reset(self):
        return {
            "reset": True,
            "url": "about:blank",
            "profile_dir": None,
            "stale_lock_cleared": False,
        }


def test_browser_tools_registered_with_expected_risk_levels():
    registry = build_default_registry(browser_manager=_StubBrowserManager())
    by_name = {tool.name: tool for tool in registry.list_all()}

    assert by_name["browser_navigate"].risk_level == "medium"
    assert by_name["browser_screenshot"].risk_level == "medium"
    assert by_name["browser_click"].risk_level == "medium"
    assert by_name["browser_type"].risk_level == "medium"
    assert by_name["browser_get_text"].risk_level == "low"
    assert by_name["browser_snapshot"].risk_level == "low"
    assert by_name["browser_reset"].risk_level == "low"


def test_browser_manager_lazy_init_and_actions():
    from app.services.tools import browser_tool as browser_tool_module

    state = {"launches": 0, "contexts": 0, "stopped": 0}

    class _FakeAccessibility:
        async def snapshot(self):
            return {"role": "WebArea"}

    class _FakePage:
        def __init__(self):
            self.url = ""
            self.accessibility = _FakeAccessibility()

        async def goto(self, url: str, wait_until: str, timeout: int):
            _ = wait_until, timeout
            self.url = url

        async def title(self):
            return "Fake Title"

        async def screenshot(self, full_page: bool = True):
            _ = full_page
            return b"png-bytes"

        async def click(self, selector: str, timeout: int):
            _ = selector, timeout

        async def fill(self, selector: str, text: str, timeout: int):
            _ = selector, text, timeout

        async def inner_text(self, selector: str, timeout: int):
            _ = selector, timeout
            return "Body Text"

        async def text_content(self, selector: str, timeout: int):
            _ = selector, timeout
            return "Node Text"

        async def close(self):
            return None

    class _FakeBrowserContext:
        def __init__(self):
            self.pages = []

        async def add_init_script(self, script: str):
            _ = script
            return None

        async def new_page(self):
            return _FakePage()

        async def close(self):
            return None

    class _FakeBrowser:
        async def new_context(self, **kwargs):
            _ = kwargs
            state["contexts"] += 1
            return _FakeBrowserContext()

        async def close(self):
            return None

    class _FakeChromium:
        async def launch_persistent_context(self, user_data_dir: str, **kwargs):
            _ = user_data_dir, kwargs
            state["contexts"] += 1
            return _FakeBrowserContext()

        async def launch(self, **kwargs):
            _ = kwargs
            state["launches"] += 1
            return _FakeBrowser()

    class _FakePlaywright:
        def __init__(self):
            self.chromium = _FakeChromium()

        async def stop(self):
            state["stopped"] += 1

    class _FakeContext:
        async def start(self):
            return _FakePlaywright()

    old_factory = browser_tool_module.async_playwright
    browser_tool_module.async_playwright = lambda: _FakeContext()
    try:
        manager = BrowserManager()
        navigate = _run(manager.navigate("https://example.com"))
        text = _run(manager.get_text())
        snap = _run(manager.get_snapshot())
        screenshot = _run(manager.screenshot())
        _run(manager.close())
    finally:
        browser_tool_module.async_playwright = old_factory

    assert navigate["title"] == "Fake Title"
    assert text["text"] == "Body Text"
    assert snap["snapshot"]["role"] == "WebArea"
    assert base64.b64decode(screenshot["image_base64"]) == b"png-bytes"
    assert state["launches"] in {0, 1}
    assert state["contexts"] == 1
    assert state["stopped"] == 1


def test_browser_manager_falls_back_when_profile_is_locked():
    from app.services.tools import browser_tool as browser_tool_module

    state = {"launches": 0}

    class _FakePage:
        url = "https://example.com"
        accessibility = None

        async def goto(self, url: str, wait_until: str, timeout: int):
            _ = url, wait_until, timeout

        async def title(self):
            return "Fallback Title"

    class _FakeBrowserContext:
        pages = []

        async def add_init_script(self, script: str):
            _ = script

        async def new_page(self):
            return _FakePage()

        async def close(self):
            return None

    class _FakeBrowser:
        async def new_context(self, **kwargs):
            _ = kwargs
            return _FakeBrowserContext()

        async def close(self):
            return None

    class _FakeChromium:
        async def launch_persistent_context(self, user_data_dir: str, **kwargs):
            _ = user_data_dir, kwargs
            raise RuntimeError("ProcessSingleton lock")

        async def launch(self, **kwargs):
            _ = kwargs
            state["launches"] += 1
            return _FakeBrowser()

    class _FakePlaywright:
        def __init__(self):
            self.chromium = _FakeChromium()

        async def stop(self):
            return None

    class _FakeContext:
        async def start(self):
            return _FakePlaywright()

    old_factory = browser_tool_module.async_playwright
    browser_tool_module.async_playwright = lambda: _FakeContext()
    try:
        manager = BrowserManager(user_data_dir="/tmp/profile")
        result = _run(manager.navigate("https://example.com"))
        _run(manager.close())
    finally:
        browser_tool_module.async_playwright = old_factory

    assert result["title"] == "Fallback Title"
    assert state["launches"] == 1


def test_browser_navigate_rejects_non_http_urls():
    manager = BrowserManager()
    try:
        _run(manager.navigate("file:///tmp/test.html"))
        raised = False
    except ValueError:
        raised = True
    assert raised is True


def test_browser_click_rejects_empty_selector():
    manager = BrowserManager()
    try:
        _run(manager.click("   "))
        raised = False
    except ValueError:
        raised = True
    assert raised is True


def test_browser_snapshot_rejects_unexpected_payload_fields():
    registry = build_default_registry(browser_manager=_StubBrowserManager())
    executor = ToolExecutor(registry)
    try:
        _run(executor.execute("browser_snapshot", {"unexpected": True}, allow_high_risk=True))
        raised = False
    except ToolValidationError:
        raised = True
    assert raised is True


def test_browser_reset_tool_executes_without_payload():
    registry = build_default_registry(browser_manager=_StubBrowserManager())
    executor = ToolExecutor(registry)
    result, _ = _run(executor.execute("browser_reset", {}, allow_high_risk=True))
    assert result["reset"] is True
    assert result["url"] == "about:blank"
