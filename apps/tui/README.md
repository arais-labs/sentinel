# Sentral terminal chat

A standalone terminal app using the same Sentral engine and existing `host_runtime`
and `http_request` tools as Sentinel. It does not start Sentinel, load its database,
read its settings or credentials, or attach to a workspace.

## Install from this checkout

```sh
make tui
```

`make tui` installs the locked dependencies with `uv` when needed. Run
`make tui ARGS="--help"` for all CLI options.

Choose OpenAI, Anthropic, or Gemini, supply an API key, and select Fast, Normal,
or Deep Think. Model and reasoning defaults are shared with Sentinel. Custom models
and compatible endpoints are available under Advanced. Keys can also come from
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `GEMINI_API_KEY`; they
are never saved. To skip setup, supply the key through the environment and run
`make tui ARGS="--provider openai --model YOUR_MODEL"`.
`--base-url` selects an OpenAI-compatible endpoint. Conversation history remains
in memory and ends when the app exits.

Send a message while a turn runs to steer it. Ctrl+C stops generation; already
started commands remain available through host_runtime list/poll. Ctrl+Q exits
and terminates processes owned by this app. `/new` clears an idle conversation;
`/help` displays controls. Commands, process termination and HTTP requests require
an explicit Allow once / Deny decision showing their arguments. Polling/listing
owned processes do not require approval.

Shared engine, providers and tool implementations live in `packages/sentral/src/sentral`.
Both the backend and this app depend on that package. The TUI
owns UI, approvals, process lifetime and configuration; backend code never imports
this app. It provides local command execution, not VM/workspace management.

## Offline tests

```sh
uv run --project apps/tui --locked --extra test pytest apps/tui/tests
```

## OAuth

Choose **Use existing CLI login (OAuth)** to import a Codex, Claude Code, or
Antigravity login. Import happens only when you press **Start chatting**. Sign in
with the matching CLI first; Sentinel’s saved credentials and database are not used.
Antigravity import currently requires macOS Keychain, as in Sentinel. Imported
credentials stay in memory; Claude renewal and Gemini refresh use the shared
provider implementations.
