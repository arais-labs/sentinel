---
sidebar_position: 5
title: Telegram Integration
---

# Telegram Integration

Sentinel ships a Telegram bridge that connects a bot to a Sentinel instance.
Telegram is **per-instance**: each instance runs its own bot, configured on that
instance's **Telegram** page (`/instances/:instanceName/telegram`). The API is
mounted under the instance-scoped prefix
`/api/v1/instances/{instance_name}/telegram`.

Because every instance has its own bot token, its own database, and its own LLM
provider, messages to an instance's bot are handled entirely by that instance —
there is no shared bot and no cross-instance routing.

## How it works

- Each instance has its **own** Telegram bot (its own
  [@BotFather](https://t.me/botfather) token).
- The bridge for an instance starts automatically as soon as that instance has a
  bot token configured, and stops when the token is removed.
- Inbound messages are routed to that instance's agent using its LLM provider,
  sessions, and memory.

## Setup

1. Create a Telegram bot via [@BotFather](https://t.me/botfather) and copy the bot
   token. Use a **separate bot per instance**.
2. Open the target instance's **Telegram** page in the UI and paste the token
   (this calls `POST /telegram/configure`), or call the endpoint directly.
3. The bridge starts automatically once a token is configured. Send `/start` to
   your bot to connect a chat.

:::note Token is not an environment variable
Like LLM provider credentials, the Telegram bot token is stored in the instance
database, not in `.env`. Configure it through the UI or the `/telegram/configure`
endpoint.
:::

## Controls

- **Configure a token** — `POST /telegram/configure` brings the bot online.
- **Start / stop the bridge** — `POST /telegram/start`, `POST /telegram/stop`.
- **Bind / unbind the owner chat** — `POST /telegram/owner`, `DELETE /telegram/owner`.
- **Remove the token** — `DELETE /telegram/configure`.
- **Read status** — `GET /telegram/status` returns the running flag, bot username,
  connected chats, masked token, owner binding, and the resolved owner main
  session ID.

## Channel routing

The bridge applies deterministic per-channel routing:

| Chat type | Routing | Trust level |
|---|---|---|
| Owner private DM | Owner's selected session (main by default), agent replies inline | Owner channel |
| Non-owner private DM | Dedicated private session per user | Untrusted by default |
| Group / supergroup | Dedicated channel session per group | Untrusted multi-party |

The owner is the Telegram account bound via the owner-binding flow.

### Owner binding

1. Start a private DM with your bot and send `/start`.
2. Open the instance's **Telegram** page → bind owner, and pick the connected chat
   (this calls `POST /telegram/owner` with the chat ID).

Owner DMs route to the selected owner session, using the main session by default.
Use `/session` to choose another conversation. Replies use the same Telegram chat
and the configured owner policies.

### Group behavior

Group messages route to the group's dedicated channel session, are treated as
untrusted multi-party input, withhold secrets/credentials, require explicit owner
approval for privileged actions, and reply back to the group.

### Non-owner DM behavior

Each non-owner DM gets its own isolated, untrusted session, with secrets and
privileged capabilities restricted unless the owner grants elevated trust.

## Bridge mechanics

- **Steering and switching.** A message sent to a busy conversation is persisted
  as steering for that conversation. `/session` remains responsive during a turn;
  switching changes the destination of subsequent messages without cancelling the
  old turn. Different conversations can proceed independently, while the run
  registry prevents duplicate turns on one conversation.
- **Rich replies.** Replies and Telegram module sends use native Rich Messages,
  preserving Markdown headings, lists, tables, quotes, and fenced code blocks.
  Requires Telegram Bot API 10.1 or newer.
- **Streaming.** Owner DMs use an ephemeral rich draft, updated at most once per
  second and refreshed during long tool calls. The final answer is sent once as
  a persistent rich message. Tool progress replaces the draft status rather than
  posting individual tool outputs.
- **Delivery.** Rate-limited sends honor Telegram's retry delay (up to three
  attempts). Ambiguous network timeouts are surfaced rather than automatically
  resending a message that Telegram may already have accepted.
- **Client library.** Rich endpoints use the library's `do_api_request`
  extension where a typed method is not available. The dependency lockfiles,
  rather than this guide, define the shipped client version.
- **Operator audit line.** When a reply is sent to a group or non-owner DM, a
  single-line audit entry is added to the Sentinel UI thread to keep the shared
  operator thread clean while recording what was sent where.

See also: [Multi-instance architecture](./multi-instance.md) ·
[Sessions](../concepts/sessions.md) ·
[API reference](../reference/api.md)
