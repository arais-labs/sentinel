---
title: Notifications
---

# Notifications

The desktop owns the inbox so workspace progress continues across backend reloads.
The last 200 entries are saved in `notifications.json` in the app's user-data folder.
Read/dismiss state persists. Delivery preferences affect banners and sounds, not storage.

## Publishing

Desktop systems call `DesktopManager.notifications.publish(input)`. Backend systems call
`app.services.notifications.publish_notification(Notification(...))`. Both use the same
source, key, title, message, severity, progress, and target contract in
`apps/desktop/sentinel/src/shared/notifications.ts`.

Use a stable `source` for the subsystem and a `key` for an ongoing job or alert.
Publishing the same source/key updates the existing entry. Identical updates are ignored;
progress updates stay read/dismissed until the status changes. The desktop streams changes
through IPC to the inbox. Optional targets link to an instance and session or workspace.
Publishers do not choose sounds or override user preferences.

Backend transport is `POST /v1/notifications` over the authenticated desktop Unix socket.
It needs no running workspace and uses no database connection. Failed delivery raises an
error; callers must not claim the notification was sent. A new source needs no UI changes.

## Existing publishers

- Workspace lifecycle: kernel/image preparation, setup stages, readiness, failure and stop.
  Preparation updates replace one entry; deleting the workspace dismisses that entry.
- Agent: `notification` with `action: send`, `title`, `message`, optional `severity`
  (`info`, `warning`, `urgent`), and optional deduplication `key`. The tool attaches its
  originating instance/session automatically. It sends immediately; future reminders
  should use a trigger invoking the tool at the intended time.
- Session completion: a linked inbox entry alongside the existing completion sound.

Progress never produces repeated banners. Urgent, warning, and error alerts can play the
selected sound. Repeated updates to the same alert ring at most once per minute.
