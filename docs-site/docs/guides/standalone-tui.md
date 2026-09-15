---
title: Standalone TUI
---

# Standalone TUI

The standalone terminal app runs Sentral with local command execution and HTTP
requests. It does not start Sentinel's backend, open its databases, use its saved
credentials, or attach to a workspace. Commands run on the machine launching it.

## Install from source

```bash
make tui
```

Choose OpenAI, Anthropic, or Gemini and select Fast, Normal, or Deep Think.
The app uses the same model and reasoning defaults as Sentinel. Paste an API key,
or supply `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `GEMINI_API_KEY` through the
environment. Custom model IDs and OpenAI-compatible endpoints are under Advanced.
Credentials and conversation history stay in memory for this process only.

## Controls

| Control | Action |
| --- | --- |
| Send a message during a turn | Steer ongoing work |
| Ctrl+C | Stop generation; already-started commands remain available to poll |
| Ctrl+Q | Exit and terminate processes owned by this TUI |
| `/new` | Clear an idle conversation |
| `/help` | Show controls |

Commands, termination, and HTTP requests show **Allow once / Deny** with their
arguments. Listing and polling the TUI's owned processes do not require approval.
The tools are the existing `host_runtime` and `http_request`; the TUI is not a
second workspace manager. See its source README for development tests.

## OAuth

Choose **Use existing CLI login (OAuth)** to import a Codex, Claude Code, or
Antigravity login. Import happens only when you press **Start chatting**. Sign in
with the matching CLI first; Sentinel’s saved credentials and database are not used.
Antigravity import currently requires macOS Keychain, as in Sentinel. Imported
credentials stay in memory; Claude renewal and Gemini refresh use the shared
provider implementations.
