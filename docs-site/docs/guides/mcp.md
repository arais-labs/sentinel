---
title: MCP servers
---

Connect an MCP server in **Settings → MCP servers** to give an instance's agents access to external tools.

## Add a connection

1. Choose **Add server** and enter a name.
2. Enter the remote server URL. Sentinel detects Streamable HTTP or legacy SSE automatically.
3. Choose **Use local command** only for a server communicating over stdin and stdout, then enter its executable and arguments.
4. Add custom authentication headers or environment variables under **Advanced** when a server requires them.
5. Choose **Connect**. If the server supports browser sign-in, finish authorization in the browser and return to Sentinel.

Local commands run on the machine hosting the Sentinel backend, not in a workspace VM or on its SSH machine. Install the server's executable and dependencies on that host first. Testing a local connection starts that program. The command is launched directly, without a shell.

Connection details and OAuth tokens are encrypted in the instance database. The settings list never returns stored credential values. Choose **Edit** to change a connection; existing credentials remain saved unless you replace or remove them. Changing the address disables the tools until the new connection succeeds.

## Tool calls and permissions

MCP tools use Sentinel's normal tool approval flow. Calls require approval by default; existing session permissions and agent modes still apply. Server-provided read-only hints do not automatically grant permission.

Connections are isolated by chat session and reused between calls. Idle connections close after 15 minutes. Disabling, replacing, or removing a connection closes its active sessions. A failed or interrupted call is not automatically repeated because it may already have changed something remotely.

Choose **Connect** again to refresh a server's tool catalog. Enabled tools are available throughout the current instance. MCP connections are included in the **Modules** backup selection.

## How tools reach the model

Connected servers are listed in the agent's system prompt by name and tool count, but their tool schemas are not sent on every turn. When a task needs a server, the agent calls `catalog_load` with the server id and that server's tools join the conversation's tool list for the rest of the session. `catalog_describe` lists a server's tools with one-line descriptions when the agent is unsure which server fits.

A loaded server drops out of the prompt again after six assistant messages without a call to it, and compaction clears loaded servers so the agent reloads what it still needs. Turn on **Always load tools** for a server you use in most conversations: its tools are in every prompt and never expire. Keep it off for large servers to save context and keep tool selection sharp.

## Supported capabilities

Sentinel supports MCP tool discovery and execution over stdio, legacy SSE, and Streamable HTTP, with automatic remote transport detection and protocol negotiation provided by the MCP SDK. Text, structured results, and inline images are handled. Binary resource and audio payloads are omitted from model context.

Remote servers can use browser-based MCP OAuth with dynamic client registration and token refresh. Workspace-hosted command execution, per-workspace server selection, MCP prompts/resources browsing, sampling, and elicitation are not implemented yet.
