---
sidebar_position: 5
title: Telegram Integration
---

# Telegram Integration

Sentinel can receive and respond to Telegram messages. Each chat type routes to a distinct session, with different trust levels and behavior.

---

## Setup

1. Create a Telegram bot via [@BotFather](https://t.me/botfather) and get the bot token
2. Add `TELEGRAM_BOT_TOKEN` to your `.env` (or set it in Settings → Telegram in the UI)
3. Start or restart the stack — the bot comes online automatically

---

## Routing by chat type

| Chat type | Where it routes | Trust level |
|---|---|---|
| Owner private DM | Owner's main session | Owner channel routing to main session |
| Non-owner private DM | Dedicated private session per user | Untrusted by default |
| Group / supergroup | Dedicated channel session per group | Untrusted multi-party |

The owner is the Telegram account linked to the Sentinel instance via the owner binding flow.

---

## Owner binding

To link your Telegram account as the owner:

1. Start a private DM with your bot
2. Send `/start`
3. Go to Sentinel Settings → Telegram → Bind owner
4. Enter the chat ID from the `/start` response

Once bound, your Telegram DMs route directly to your main session and receive inline replies from the agent.

---

## Owner DM behavior

When you message the bot from your owner DM:

- Routed to your main session
- Agent responds inline in the same Telegram chat
- Owner DM follows owner policy defaults and still respects configured guardrails.

---

## Group chat behavior

When the bot receives a message in a group:

- Routed to the group's dedicated channel session
- Treated as untrusted multi-party input
- Agent **will not reveal secrets or credentials** in group responses
- Agent will not take privileged actions without explicit owner approval
- Reply is sent back to the group chat

If a group message mentions the bot or requires a response, the agent always replies to the group — not to the operator UI.

---

## Non-owner DM behavior

Non-owner private DMs behave like group chats:

- Each user gets their own isolated session
- Treated as untrusted by default
- Secrets and privileged capabilities are restricted
- The owner can explicitly grant elevated trust to specific users

---

## Concurrency and busy state

Telegram sessions use a run registry to prevent concurrent agent runs on the same session. If the agent is already processing a message when a new one arrives:

- The bridge polls for up to 60 seconds (12 attempts × 5 second intervals)
- If the agent is still busy after polling, the new message is rejected for now
- The user receives a busy message and can retry

---

## Message length

Telegram has a hard 4096-character limit per message. Long agent responses are automatically split into multiple messages.

---

## Telegram operator audit

When the agent sends a reply to a group or non-owner DM, a single-line audit entry is added to the Sentinel UI thread:

```
Telegram audit: sent reply to chat_id=<id> (<group_or_dm>)
```

This keeps the shared operator thread clean while maintaining a record of what was sent where.
