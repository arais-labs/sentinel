---
sidebar_position: 6
title: Browser Automation
---

# Browser Automation

Sentinel includes a full Playwright browser runtime. Agents browse the web, interact with pages, extract data, and take screenshots — all autonomously. Operators can watch execution live.

---

## How it works

A persistent Chromium instance runs inside the Docker stack. Agents control it via browser tools. The browser state (open tabs, cookies, auth sessions) persists across tool calls within a turn.

Operators can watch the browser live via the VNC view at `/vnc/`.

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

The agent uses `browser_snapshot` with `interactive_only=true` as the most token-efficient way to find clickable and fillable elements. For reading page content, it uses `browser_get_text`.

---

## Available tools

| Tool | Description |
|---|---|
| `browser_navigate` | Navigate to a URL |
| `browser_snapshot` | Read the accessibility tree |
| `browser_get_text` | Extract visible text from the page or a selector |
| `browser_screenshot` | Capture a screenshot |
| `browser_click` | Click an element by selector or accessible name |
| `browser_type` | Type text into an element |
| `browser_fill_form` | Fill a multi-field form in one call |
| `browser_select` | Select an option in a native dropdown |
| `browser_scroll` | Scroll the page or an element |
| `browser_press_key` | Press a keyboard key |
| `browser_wait_for` | Wait for an element state before continuing |
| `browser_get_value` | Read the current value of a form field |
| `browser_tabs` | List open tabs |
| `browser_tab_open` | Open a new tab |
| `browser_tab_focus` | Switch to a tab |
| `browser_tab_close` | Close a tab |
| `browser_reset` | Reset the browser to a clean state |

---

## Screenshots and image context

Screenshots taken during a turn are automatically reinjected into the next LLM call so the agent can reason about visual page state.

There are two hard limits on this:

| Limit | Effect when exceeded |
|---|---|
| Max image count per context | Older images are silently dropped |
| Max total image bytes per context | Images are dropped to stay within the byte limit |

:::warning
If you notice the agent making decisions that seem to ignore recent browser state, images may have been silently dropped due to these limits. Use `browser_get_text` or `browser_snapshot` as a text-based fallback when visual reasoning is critical.
:::

---

## Tab management

Multiple tabs can be open simultaneously. After any action that triggers a popup or opens a new tab, the agent calls `browser_tabs` to list open tabs and `browser_tab_focus` to switch to the correct one before continuing.

---

## Selectors

Browser tools accept two selector formats:

- **CSS selectors** — standard CSS (`#id`, `.class`, `button[type=submit]`)
- **Accessibility selectors** — human-readable names from the snapshot, e.g. `button: Accept`, `textbox: Email`, `link: Sign in`

Accessibility selectors are preferred because they are stable across minor UI changes and match exactly what is returned by `browser_snapshot`.

---

## Handling slow pages

By default, browser actions use a standard timeout. For slow pages, pass `timeout_ms` to increase the wait window:

```
browser_click(selector="button: Submit", timeout_ms=10000)
```

---

## Human verification steps

Some flows require human interaction — captchas, OTP codes, phone verification. The agent will pause and ask the operator for the required value. The agent cannot bypass these automatically.

---

## Live monitoring

The VNC view at `http://localhost:4747/vnc/` shows the agent's browser in real time. Use it to:

- Watch a complex form flow execute
- Debug unexpected navigation
- Verify the agent is interacting with the right elements
- Demonstrate agent behavior to stakeholders
