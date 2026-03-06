---
sidebar_position: 5
title: Browser Automation
---

# Browser Automation

Sentinel includes a full Playwright browser runtime. Agents can browse the web, interact with pages, and extract data — all autonomously.

---

## How it works

The browser runs inside the Docker stack as a persistent Chromium instance. Agents interact with it via browser tools — navigate, click, type, extract, screenshot. Operators can watch execution live via the VNC view at `/vnc/`.

---

## Agent browser flow

The standard agent browser pattern:

```
navigate → snapshot → interact → verify → continue
```

1. **Navigate** to a URL
2. **Snapshot** the accessibility tree to understand page structure
3. **Interact** — click, type, select
4. **Verify** — read back values or take a screenshot
5. **Continue** — next step based on page state

---

## Capabilities

| Capability | Description |
|---|---|
| Navigation | Full URL navigation, tab management |
| Interaction | Click, type, select, scroll, key presses |
| Extraction | Text content, accessibility tree, element values |
| Screenshots | Full page or viewport captures |
| Forms | Multi-field form flows in a single operation |
| Waiting | Wait for element states before proceeding |

---

## Live monitoring

The VNC view at `http://localhost:4747/vnc/` lets operators watch the agent's browser in real time — useful for debugging complex flows or demonstrating agent behavior.
