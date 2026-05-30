---
sidebar_position: 6
title: Browser Automation
---

# Browser Automation

Sentinel ships a Playwright-based browser tool. Agents browse the web, interact with
pages, extract data, and take screenshots — all autonomously. The browser runs as a
**visible Chromium** on the instance's SSH runtime target, so operators can watch
execution live through the runtime desktop view.

---

## Where the browser runs

The browser is **not** a separate service in the Docker stack. Each agent session gets
its own Chromium process, launched on the **per-instance SSH/tmux runtime target** under
that runtime's X display, inside the session's workspace directory. Sentinel controls it
over the Chrome DevTools Protocol (CDP) through an SSH-forwarded local port.

Two consequences follow from this design:

- **A configured SSH runtime is required.** Browser tools resolve against the runtime
  attached to the instance. With no SSH runtime configured for the instance, the browser
  pool cannot start a session and browser commands fail. See
  [Runtime Exec Security](../guides/runtime-exec-security.md) for how runtimes are managed
  and sandboxed.
- **Browser state is per session and per instance.** The pool is keyed by
  `(instance, session)`. Open tabs, cookies, and the persistent Chromium profile live in
  that session's workspace on its runtime target and survive across tool calls within a
  turn. Different instances and different sessions never share a browser context.

:::note Multi-instance
Sentinel hosts multiple instances in one deployment. Browser sessions are isolated
per instance — each instance drives its own runtime target, and one instance can never
see or control another instance's browser. The desktop app follows the same model: the
browser lives on whatever SSH runtime the instance is configured against, not inside the
desktop shell itself.
:::

---

## Standard execution flow

```
navigate → snapshot → interact → verify → continue
```

1. **Navigate** — go to a URL or open a new tab
2. **Snapshot** — read the accessibility tree to understand page structure
3. **Interact** — click, type, select, scroll, press keys
4. **Verify** — read back values or take a screenshot to confirm state
5. **Continue** — next step based on what the page shows

The agent uses `snapshot` with `interactive_only=true` as the most token-efficient way to
find clickable and fillable elements — it drops non-interactive nodes from the
accessibility tree. For reading page content, it uses `get_text`, and large results are
truncated with a notice that prompts the agent to narrow its selector.

---

## The `browser` tool

Browser automation is exposed as a **single `browser` module** invoked with a `command`
argument (for example, `command=navigate`, `command=click`). It is one of Sentinel's
built-in system modules. The supported commands are:

| Command | Description |
|---|---|
| `navigate` | Navigate the current tab to a URL |
| `snapshot` | Read the accessibility tree (use `interactive_only=true` to trim) |
| `get_text` | Extract visible text from the page or a selector |
| `get_html` | Read raw HTML for the page or a selector |
| `screenshot` | Capture a screenshot (reinjected as image context) |
| `click` | Click an element by selector or accessible name |
| `type` | Type text into an element |
| `fill_form` | Fill a multi-field form in one call |
| `select` | Select an option in a native dropdown |
| `scroll` | Scroll the page or an element |
| `press_key` | Press a keyboard key |
| `wait_for` | Wait for an element state before continuing |
| `get_value` | Read the current value of a form field |
| `evaluate` | Evaluate a JavaScript expression in the page |
| `get_cookies` | Read cookies for the current context |
| `set_cookies` | Set cookies in the current context |
| `console_logs` | Read collected browser console output |
| `network_intercept` | Stub or modify matching network requests |
| `network_logs` | Read collected network activity |
| `clear_network_intercepts` | Remove active network intercepts |
| `tabs` | List open tabs |
| `tab_open` | Open a new tab |
| `tab_focus` | Switch to a tab |
| `tab_close` | Close a tab |
| `reset` | Reset the session's browser to a clean state |

Like all modules, `browser` commands run under the instance's
[permission policy](modules-and-permissions.md). Sensitive actions can be gated behind
approval depending on configuration.

---

## Selectors

Browser commands accept two selector formats:

- **CSS selectors** — standard CSS (`#id`, `.class`, `button[type=submit]`)
- **Accessibility selectors** — human-readable role/name pairs from the snapshot, e.g.
  `button: Accept`, `textbox: Email`, `link: Sign in`

Accessibility selectors are preferred because they are stable across minor UI changes and
match exactly what `snapshot` returns. Interactive roles recognized by the snapshot filter
include `button`, `link`, `textbox`, `searchbox`, `checkbox`, `radio`, `combobox`,
`listbox`, `menuitem`, `slider`, `tab`, `switch`, `option`, and more.

---

## Tab management

Multiple tabs can be open simultaneously. After any action that triggers a popup or opens
a new tab, the agent calls `tabs` to list open tabs and `tab_focus` to switch to the
correct one before continuing. Most page-level commands also accept an optional `tab_id`
so the agent can target a specific tab without changing focus.

---

## Screenshots and image context

Screenshots taken during a turn are reinjected into the next LLM call so the agent can
reason about visual page state. Reinjection is bounded so a burst of screenshots cannot
blow out the context window:

| Limit | Effect when exceeded |
|---|---|
| Max images per turn (default 2) | Older images are dropped; a note records how many were skipped |
| Max bytes per image (default ~2 MB) | Oversized images are skipped rather than sent |

:::warning
If the agent makes decisions that seem to ignore recent browser state, screenshots may
have been dropped under these limits. Use `get_text` or `snapshot` as a text-based
fallback when visual reasoning is critical.
:::

---

## Stealth mode

Sentinel applies a stealth init script to the browser context that normalizes common
automation fingerprints: it clears `navigator.webdriver`, sets `navigator.language` /
`navigator.languages` and timezone from the configured locale, and stubs `window.chrome`.
Locale and timezone default to `en-US` / `America/Los_Angeles` and can be overridden via
`BROWSER_LOCALE` and `BROWSER_TIMEZONE_ID`. This reduces (but does not eliminate)
bot-detection signals.

---

## Handling slow pages

Browser commands use a standard timeout by default. For slow pages, pass `timeout_ms` to
extend the wait window:

```
browser command=click selector="button: Submit" timeout_ms=10000
```

---

## Human verification steps

Some flows require human interaction — captchas, OTP codes, phone verification. The agent
cannot bypass these automatically; it will surface the blocker and ask the operator for
the required value (or stop). Plan flows so a human can step in when the browser hits one
of these gates.

---

## Live monitoring

Because Chromium runs visibly on the runtime's desktop, operators can watch it live from
the **session view**. The right pane of the Sessions page can stream the runtime desktop
over a VNC-RFB WebSocket — the same desktop the agent's browser is drawing into — so you
see clicks, navigation, and form fills as they happen.

Use the live desktop view to:

- Watch a complex form flow execute
- Debug unexpected navigation
- Confirm the agent is interacting with the right elements

:::warning Limitations
- **The live view is the runtime's desktop, not a dedicated browser stream.** There is no
  `/vnc/` HTTP page; the desktop is bridged through the instance-scoped runtime
  WebSocket (`/api/v1/instances/{instance_name}/runtime/live-view/{session_id}/rfb`) and
  surfaced in the Sessions UI. It is only available when the instance has an SSH runtime
  configured and its desktop session can start.
- **The browser tool itself has no built-in VNC or display server.** Visibility comes
  entirely from the runtime desktop layer; the Playwright tool only speaks CDP. If the
  runtime cannot provide a desktop, the agent can still drive the browser headlessly, but
  there is nothing to watch.
:::
