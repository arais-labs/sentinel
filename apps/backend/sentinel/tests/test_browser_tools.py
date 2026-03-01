from __future__ import annotations

import asyncio
import base64

from app.services.tools.builtin import build_default_registry
from app.services.tools.browser_tool import BrowserManager
from app.services.tools.executor import ToolExecutor, ToolValidationError


def _run(coro):
    return asyncio.run(coro)


class _StubBrowserManager:
    async def navigate(
        self,
        url: str,
        *,
        timeout_ms: int | None = None,
        tab_id: str | None = None,
    ):
        _ = timeout_ms, tab_id
        return {"url": url, "title": "Example"}

    async def screenshot(self, *, full_page: bool = True, tab_id: str | None = None):
        _ = full_page, tab_id
        return {"image_base64": base64.b64encode(b"fake").decode("ascii")}

    async def click(
        self,
        selector: str,
        *,
        timeout_ms: int | None = None,
        tab_id: str | None = None,
    ):
        _ = timeout_ms, tab_id
        return {"clicked": True, "selector": selector}

    async def type_text(
        self,
        selector: str,
        text: str,
        *,
        timeout_ms: int | None = None,
        tab_id: str | None = None,
    ):
        _ = timeout_ms, tab_id
        return {"typed": True, "selector": selector, "characters": len(text)}

    async def select_option(
        self,
        selector: str,
        *,
        value: str | None = None,
        label: str | None = None,
        index: int | None = None,
        timeout_ms: int | None = None,
        tab_id: str | None = None,
    ):
        _ = timeout_ms, tab_id
        return {
            "selector": selector,
            "selected_values": [value or label or str(index)],
            "criteria": {"value": value, "label": label, "index": index},
            "url": "https://example.com",
            "title": "Example",
            "tab_id": "t1",
        }

    async def wait_for(
        self,
        selector: str,
        *,
        condition: str = "visible",
        timeout_ms: int | None = None,
        tab_id: str | None = None,
    ):
        _ = tab_id
        return {
            "selector": selector,
            "condition": condition,
            "timeout_ms": timeout_ms,
            "satisfied": True,
            "url": "https://example.com",
            "title": "Example",
            "tab_id": "t1",
        }

    async def get_value(self, selector: str, *, tab_id: str | None = None):
        _ = tab_id
        return {"selector": selector, "found": True, "value": "stubbed"}

    async def fill_form(
        self,
        steps: list[dict],
        *,
        continue_on_error: bool = False,
        verify: bool = False,
        tab_id: str | None = None,
    ):
        _ = tab_id
        return {
            "ok": True,
            "total": len(steps),
            "completed": len(steps),
            "failed": 0,
            "steps": steps,
            "continue_on_error": continue_on_error,
            "verify": verify,
            "url": "https://example.com",
            "title": "Example",
            "tab_id": "t1",
        }

    async def get_text(self, selector: str | None = None, *, tab_id: str | None = None):
        _ = tab_id
        return {"selector": selector, "text": "hello"}

    async def get_snapshot(
        self,
        *,
        interactive_only: bool = False,
        max_depth: int | None = None,
        tab_id: str | None = None,
    ):
        _ = interactive_only, max_depth, tab_id
        return {"snapshot": {"role": "WebArea"}}

    async def press_key(self, key: str, *, tab_id: str | None = None):
        _ = tab_id
        return {"pressed": True, "key": key}

    async def reset(self):
        return {
            "reset": True,
            "url": "about:blank",
            "profile_dir": None,
            "stale_lock_cleared": False,
        }

    async def list_tabs(self):
        return {
            "tabs": [
                {
                    "tab_id": "t1",
                    "title": "Example",
                    "url": "https://example.com",
                    "active": True,
                }
            ],
            "active_tab_id": "t1",
        }

    async def open_tab(self, url: str = "about:blank"):
        return {
            "opened": True,
            "tab_id": "t2",
            "url": url,
            "title": "",
        }

    async def focus_tab(self, tab_id: str):
        return {
            "focused": True,
            "tab_id": tab_id,
            "url": "https://example.com",
            "title": "Example",
        }

    async def close_tab(self, tab_id: str):
        return {
            "closed": True,
            "tab_id": tab_id,
            "active_tab_id": "t1",
        }


def test_browser_tools_registered_with_expected_risk_levels():
    registry = build_default_registry(browser_manager=_StubBrowserManager())
    by_name = {tool.name: tool for tool in registry.list_all()}

    assert by_name["browser_navigate"].risk_level == "medium"
    assert by_name["browser_screenshot"].risk_level == "medium"
    assert by_name["browser_click"].risk_level == "medium"
    assert by_name["browser_type"].risk_level == "medium"
    assert by_name["browser_select"].risk_level == "medium"
    assert by_name["browser_wait_for"].risk_level == "low"
    assert by_name["browser_get_value"].risk_level == "low"
    assert by_name["browser_fill_form"].risk_level == "medium"
    assert by_name["browser_get_text"].risk_level == "low"
    assert by_name["browser_snapshot"].risk_level == "low"
    assert by_name["browser_reset"].risk_level == "low"
    assert by_name["browser_tabs"].risk_level == "low"
    assert by_name["browser_tab_open"].risk_level == "medium"
    assert by_name["browser_tab_focus"].risk_level == "low"
    assert by_name["browser_tab_close"].risk_level == "medium"


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
    if isinstance(snap["snapshot"], dict):
        assert snap["snapshot"]["role"] == "WebArea"
    else:
        assert "WebArea" in str(snap["snapshot"])
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


def test_browser_click_passes_optional_tab_id_to_manager():
    observed: dict[str, str | None] = {"tab_id": None}

    class _CaptureManager(_StubBrowserManager):
        async def click(
            self,
            selector: str,
            *,
            timeout_ms: int | None = None,
            tab_id: str | None = None,
        ):
            _ = timeout_ms
            observed["tab_id"] = tab_id
            return {"clicked": True, "selector": selector}

    registry = build_default_registry(browser_manager=_CaptureManager())
    executor = ToolExecutor(registry)
    result, _ = _run(
        executor.execute(
            "browser_click",
            {"selector": "button: Continue", "tab_id": "t7"},
            allow_high_risk=True,
        )
    )
    assert result["clicked"] is True
    assert observed["tab_id"] == "t7"


def test_browser_snapshot_rejects_unexpected_payload_fields():
    registry = build_default_registry(browser_manager=_StubBrowserManager())
    executor = ToolExecutor(registry)
    try:
        _run(
            executor.execute(
                "browser_snapshot", {"unexpected": True}, allow_high_risk=True
            )
        )
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


def test_browser_tabs_tool_executes_without_payload():
    registry = build_default_registry(browser_manager=_StubBrowserManager())
    executor = ToolExecutor(registry)
    result, _ = _run(executor.execute("browser_tabs", {}, allow_high_risk=True))
    assert result["active_tab_id"] == "t1"
    assert result["tabs"][0]["tab_id"] == "t1"


def test_browser_tab_open_defaults_to_blank():
    registry = build_default_registry(browser_manager=_StubBrowserManager())
    executor = ToolExecutor(registry)
    result, _ = _run(executor.execute("browser_tab_open", {}, allow_high_risk=True))
    assert result["opened"] is True
    assert result["url"] == "about:blank"


def test_browser_tab_focus_requires_tab_id():
    registry = build_default_registry(browser_manager=_StubBrowserManager())
    executor = ToolExecutor(registry)
    try:
        _run(executor.execute("browser_tab_focus", {}, allow_high_risk=True))
        raised = False
    except ToolValidationError:
        raised = True
    assert raised is True

    result, _ = _run(
        executor.execute("browser_tab_focus", {"tab_id": "t1"}, allow_high_risk=True)
    )
    assert result["focused"] is True
    assert result["tab_id"] == "t1"


def test_browser_tab_close_requires_tab_id():
    registry = build_default_registry(browser_manager=_StubBrowserManager())
    executor = ToolExecutor(registry)
    try:
        _run(executor.execute("browser_tab_close", {}, allow_high_risk=True))
        raised = False
    except ToolValidationError:
        raised = True
    assert raised is True

    result, _ = _run(
        executor.execute("browser_tab_close", {"tab_id": "t2"}, allow_high_risk=True)
    )
    assert result["closed"] is True
    assert result["tab_id"] == "t2"


def test_browser_select_requires_selector_and_choice():
    registry = build_default_registry(browser_manager=_StubBrowserManager())
    executor = ToolExecutor(registry)

    try:
        _run(executor.execute("browser_select", {}, allow_high_risk=True))
        raised = False
    except ToolValidationError:
        raised = True
    assert raised is True

    try:
        _run(
            executor.execute(
                "browser_select",
                {"selector": "combobox: Month"},
                allow_high_risk=True,
            )
        )
        raised = False
    except ToolValidationError:
        raised = True
    assert raised is True

    result, _ = _run(
        executor.execute(
            "browser_select",
            {"selector": "combobox: Month", "value": "1"},
            allow_high_risk=True,
        )
    )
    assert result["selected_values"] == ["1"]


def test_browser_wait_for_accepts_conditions():
    registry = build_default_registry(browser_manager=_StubBrowserManager())
    executor = ToolExecutor(registry)

    result, _ = _run(
        executor.execute(
            "browser_wait_for",
            {"selector": "button: Next", "condition": "enabled", "timeout_ms": 4000},
            allow_high_risk=True,
        )
    )
    assert result["satisfied"] is True
    assert result["condition"] == "enabled"


def test_browser_get_value_requires_selector():
    registry = build_default_registry(browser_manager=_StubBrowserManager())
    executor = ToolExecutor(registry)
    try:
        _run(executor.execute("browser_get_value", {}, allow_high_risk=True))
        raised = False
    except ToolValidationError:
        raised = True
    assert raised is True


def test_browser_fill_form_requires_non_empty_steps():
    registry = build_default_registry(browser_manager=_StubBrowserManager())
    executor = ToolExecutor(registry)

    try:
        _run(executor.execute("browser_fill_form", {}, allow_high_risk=True))
        raised = False
    except ToolValidationError:
        raised = True
    assert raised is True

    result, _ = _run(
        executor.execute(
            "browser_fill_form",
            {
                "steps": [
                    {"selector": "textbox: Email", "text": "qa@example.com"},
                    {"selector": "button: Continue", "click": True},
                ],
                "verify": True,
            },
            allow_high_risk=True,
        )
    )
    assert result["ok"] is True
    assert result["total"] == 2


class _SemanticLocator:
    def __init__(
        self,
        page,
        *,
        kind: str,
        role: str | None = None,
        name: str | None = None,
        query: str | None = None,
    ):
        self._page = page
        self._kind = kind
        self._role = role
        self._name = name
        self._query = query
        self._enabled = True

    @property
    def first(self):
        return self

    async def fill(self, text: str, timeout: int):
        _ = timeout
        self._page.last_fill = {
            "kind": self._kind,
            "role": self._role,
            "name": self._name,
            "query": self._query,
            "text": text,
        }
        self._page.actions.append(
            {
                "action": "fill",
                "role": self._role,
                "name": self._name,
                "query": self._query,
            }
        )

    async def click(self, timeout: int, force: bool = False):
        _ = timeout, force
        self._page.last_click = {
            "kind": self._kind,
            "role": self._role,
            "name": self._name,
            "query": self._query,
        }
        self._page.actions.append(
            {
                "action": "click",
                "role": self._role,
                "name": self._name,
                "query": self._query,
            }
        )

    async def inner_text(self, timeout: int):
        _ = timeout
        return "semantic text"

    async def select_option(
        self,
        *,
        value: str | None = None,
        label: str | None = None,
        index: int | None = None,
        timeout: int,
    ):
        _ = timeout
        selected = value or label or str(index)
        self._page.last_select = {
            "kind": self._kind,
            "role": self._role,
            "name": self._name,
            "query": self._query,
            "selected": selected,
        }
        self._page.actions.append(
            {
                "action": "select",
                "role": self._role,
                "name": self._name,
                "query": self._query,
            }
        )
        return [selected]

    async def wait_for(self, state: str, timeout: int):
        _ = timeout
        self._page.last_wait = {
            "kind": self._kind,
            "role": self._role,
            "name": self._name,
            "query": self._query,
            "state": state,
        }
        self._page.actions.append(
            {
                "action": "wait",
                "role": self._role,
                "name": self._name,
                "query": self._query,
            }
        )

    async def is_enabled(self, timeout: int):
        _ = timeout
        self._page.actions.append(
            {
                "action": "is_enabled",
                "role": self._role,
                "name": self._name,
                "query": self._query,
            }
        )
        return self._enabled

    async def evaluate(self, script: str, timeout: int):  # noqa: ARG002
        _ = timeout
        return {"tag": "input", "value": "from-locator"}


class _SemanticPage:
    def __init__(self):
        self.last_fill = None
        self.last_click = None
        self.last_select = None
        self.last_wait = None
        self.actions = []
        self.url = "https://example.com"

    def get_by_role(self, role: str, name: str, exact: bool = False):
        _ = exact
        return _SemanticLocator(self, kind="role", role=role, name=name)

    def get_by_label(self, name: str, exact: bool = False):
        _ = exact
        return _SemanticLocator(self, kind="label", name=name)

    def get_by_placeholder(self, name: str, exact: bool = False):
        _ = exact
        return _SemanticLocator(self, kind="placeholder", name=name)

    def locator(self, query: str):
        return _SemanticLocator(self, kind="locator", query=query)

    async def fill(self, selector: str, text: str, timeout: int):
        _ = timeout
        self.last_fill = {"kind": "css", "selector": selector, "text": text}

    async def click(self, selector: str, timeout: int):
        _ = timeout
        self.last_click = {"kind": "css", "selector": selector}

    async def title(self):
        return "Example"


def test_browser_type_accepts_snapshot_style_textbox_selector():
    manager = BrowserManager()
    manager._page = _SemanticPage()

    result = _run(manager.type_text("textbox: Email", "qa@example.com"))

    assert result["typed"] is True
    assert manager._page.last_fill == {
        "kind": "role",
        "role": "textbox",
        "name": "Email",
        "query": None,
        "text": "qa@example.com",
    }


def test_browser_type_accepts_aria_slash_selector():
    manager = BrowserManager()
    manager._page = _SemanticPage()

    result = _run(manager.type_text("aria/Email", "qa@example.com"))

    assert result["typed"] is True
    assert manager._page.last_fill == {
        "kind": "locator",
        "role": None,
        "name": None,
        "query": "aria=Email",
        "text": "qa@example.com",
    }


def test_browser_click_accepts_snapshot_style_button_selector():
    manager = BrowserManager()
    manager._page = _SemanticPage()

    result = _run(manager.click("button: Accept"))

    assert result["clicked"] is True
    assert manager._page.last_click == {
        "kind": "role",
        "role": "button",
        "name": "Accept",
        "query": None,
    }
    assert result["url"] == "https://example.com"
    assert result["title"] == "Example"
    assert result["tab_id"] == "t1"


def test_browser_select_accepts_semantic_combobox_selector():
    manager = BrowserManager()
    manager._page = _SemanticPage()

    result = _run(manager.select_option("combobox: Month", value="1"))

    assert result["selected_values"] == ["1"]
    assert manager._page.last_select == {
        "kind": "role",
        "role": "combobox",
        "name": "Month",
        "query": None,
        "selected": "1",
    }


def test_browser_wait_for_enabled_uses_semantic_selector():
    manager = BrowserManager()
    manager._page = _SemanticPage()

    result = _run(manager.wait_for("button: Next", condition="enabled", timeout_ms=500))

    assert result["satisfied"] is True
    assert result["condition"] == "enabled"


def test_browser_get_value_supports_semantic_selector():
    manager = BrowserManager()
    manager._page = _SemanticPage()

    result = _run(manager.get_value("textbox: Email"))

    assert result["found"] is True
    assert result["value"] == "from-locator"


class _OptionSelectPage(_SemanticPage):
    def __init__(self):
        super().__init__()
        self.selected = None

    async def select_option(self, selector: str, value: str, timeout: int):
        _ = timeout
        self.selected = {"selector": selector, "value": value}
        return [value]

    async def click(self, selector: str, timeout: int):
        _ = selector, timeout
        raise AssertionError("click() should not run for option[value=...] fallback")


def test_browser_click_option_value_uses_select_option_fallback():
    manager = BrowserManager()
    manager._page = _OptionSelectPage()

    result = _run(manager.click("option[value='2']"))

    assert result["clicked"] is True
    assert manager._page.selected == {"selector": "select", "value": "2"}


class _FormValueElement:
    def __init__(self, selector: str):
        self.selector = selector


class _FormValuePage(_SemanticPage):
    async def query_selector(self, selector: str):
        return _FormValueElement(selector)

    async def evaluate(self, script: str, element):  # noqa: ARG002
        if "textarea" in element.selector:
            return "typed textarea value"
        if "select" in element.selector:
            return "Two"
        return ""

    async def text_content(self, selector: str, timeout: int):
        _ = selector, timeout
        return ""


def test_browser_get_text_returns_form_control_values():
    manager = BrowserManager()
    manager._page = _FormValuePage()

    textarea = _run(manager.get_text("textarea[name='my-textarea']"))
    selected = _run(manager.get_text("select[name='my-select']"))

    assert textarea["text"] == "typed textarea value"
    assert selected["text"] == "Two"


def test_browser_fill_form_executes_mixed_steps_with_verify():
    manager = BrowserManager()
    manager._page = _SemanticPage()

    result = _run(
        manager.fill_form(
            [
                {"selector": "textbox: Email", "text": "qa@example.com"},
                {"selector": "combobox: Month", "value": "1"},
                {
                    "selector": "button: Next",
                    "action": "wait",
                    "condition": "enabled",
                    "timeout_ms": 500,
                },
                {"selector": "button: Next", "click": True},
            ],
            verify=True,
        )
    )

    assert result["ok"] is True
    assert result["total"] == 4
    assert result["completed"] == 4
    assert result["failed"] == 0
    assert result["steps"][0]["action"] == "type"
    assert result["steps"][0]["verify"]["found"] is True
    assert result["steps"][1]["action"] == "select"
    assert result["steps"][1]["verify"]["value"] == "from-locator"
    assert result["steps"][3]["action"] == "click"
    assert [item["action"] for item in manager._page.actions] == [
        "fill",
        "select",
        "is_enabled",
        "click",
    ]


def test_browser_fill_form_can_continue_after_step_error():
    manager = BrowserManager()
    manager._page = _SemanticPage()

    result = _run(
        manager.fill_form(
            [
                {"selector": "textbox: Email", "action": "not-a-real-action"},
                {"selector": "button: Accept", "action": "click"},
            ],
            continue_on_error=True,
        )
    )

    assert result["ok"] is False
    assert result["failed"] == 1
    assert result["completed"] == 1
    assert result["steps"][0]["ok"] is False
    assert result["steps"][1]["ok"] is True
