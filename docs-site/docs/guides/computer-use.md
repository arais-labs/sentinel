---
title: Computer use
---

# Computer use

The `computer` module controls the attached workspace's desktop. Its screenshots
and input target the same Xvnc/Xfce session shown in the live desktop pane. It does
not control the host Mac or request host Accessibility access.

## Observe, act, verify

Start with a screenshot:

```json
{"action": "screenshot"}
```

Use the returned viewport for the next action batch:

```json
{
  "action": "perform",
  "viewport": {"width": 1920, "height": 1200},
  "actions": [
    {"type": "click", "x": 300, "y": 200},
    {"type": "type", "text": "Hello"},
    {"type": "keypress", "keys": ["Return"]}
  ]
}
```

Coordinates start at the upper-left corner. The example dimensions must be
replaced with the dimensions in the actual screenshot. Actions include move,
click, double-click, drag, scroll, keypress, type, and wait. Key names use X11
notation such as `Control_L`, `Shift_L`, `Return`, and `Left`.

A request validates the batch and viewport before input. A successful batch returns
another screenshot; use it to verify the effect before continuing. If an action
fails, inspect `completed_actions` and the returned state. The failing action may
have partially executed, so do not blindly replay the batch.

## Shared desktop and cancellation

The workspace must be running with Desktop installed. Screenshot can start its
desktop; status is observational. All chats sharing a workspace also share its
windows and focus. A guest lock serializes computer batches; a competing batch
reports busy instead of running later against an old screenshot.

Cancellation targets the generated request ID and releases held buttons/keys when
the worker can clean up. A hard VM or process kill cannot guarantee cleanup.

## Implementation

The guest controller uses Xlib/XTEST for pointer and key events, `xdotool` for
literal Unicode typing, and XGetImage plus Pillow for screenshots. Image metadata
retains the viewport and pointer location. Provider adapters receive image inputs
rather than base64 embedded in prose, subject to configured attachment limits.

For web pages, the [browser module](../concepts/browser-automation.md) can inspect
DOM/accessibility state directly. Computer use is useful for native applications
or interactions that require the whole desktop.
