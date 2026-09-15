# Frontend design guidelines

Sentinel is a desktop workspace for conversations, tools, files, terminals, and live
desktops. Keep actions recognizable across panes and preserve the user's working
context when they resize, focus, or switch conversations.

These guidelines describe the existing UI. The linked components and styles are the
source of truth for appearance and behavior; extend their owners rather than copying
a second implementation into a page.

## Owners to start with

| Area | Source |
| --- | --- |
| Theme colors, base typography, scrollbars, common button classes | [index.css](src/index.css), [theme store](src/store/theme-store.ts) |
| Compact actions and dropdown triggers | [HeaderActions](src/components/ui/HeaderActions.tsx), [header styles](src/components/session/chat-header.css) |
| Pane chrome, action overflow, view selection | [PaneHeaderTab](src/components/workspace/PaneHeaderTab.tsx), [PaneActions](src/components/workspace/PaneActions.tsx) |
| View registry and layout ownership | [workspace-tabs](src/lib/workspace-tabs.tsx), [Workspace](src/components/workspace/Workspace.tsx), [workspace store](src/store/workspace-store.ts) |
| Anchored overlays | [portal-menu](src/lib/portal-menu.ts) |
| Runtime confirmation and progress | [RemoteRuntimeDialog](src/components/runtime/RemoteRuntimeDialog.tsx), [dialog styles](src/components/runtime/remote-runtime-dialog.css) |
| Workspace forms | [WorkspaceEditor](src/components/runtime/WorkspaceEditor.tsx) |
| File browsing and preview states | [WorkspaceBrowser](src/components/files/WorkspaceBrowser.tsx), [browser styles](src/components/files/workspace-browser.css) |
| Shared content and feedback | [Markdown](src/components/ui/Markdown.tsx), [StatusChip](src/components/ui/StatusChip.tsx), [NotificationHost](src/components/NotificationHost.tsx) |

## Theme and visual language

The theme store persists `light` or `dark`, defaults to dark, and updates both the
`dark` class and `data-theme` on the document root. Test both themes; do not infer the
current theme from a pane's background.

Use the existing CSS custom properties for ordinary surfaces and text:

- `--app-bg`, `--surface-0` through `--surface-3`: background and surface hierarchy.
- `--border-subtle`, `--border-strong`: boundaries and emphasis.
- `--text-primary`, `--text-secondary`, `--text-muted`: text hierarchy.
- `--accent-solid`, `--sentinel-blue`: primary actions and blue accents.
- `--scroll-thumb`, `--scroll-thumb-hover`: shared scrollbar treatment.

Tailwind accepts these directly, for example `bg-(--surface-1)` and
`text-(--text-secondary)`. Do not duplicate the token values in this document or in a
new component. Feature styles may define scoped tokens with explicit light/dark
variants; the shared header styles demonstrate this pattern.

The UI uses rounded pills, larger rounded dialogs and cards, subtle gradients,
shadows, and selected-state glow. Preserve the treatment of the surrounding surface;
a blanket flat-surface or small-radius rule does not describe Sentinel. Use existing
control classes and scoped styles instead of introducing another visual family.

Inter is the UI family and JetBrains Mono is used for code. Follow the typography of
the component being extended: compact uppercase action labels, normal-case body copy,
and larger dialog headings serve different purposes. There is no single font size or
corner radius for every page. Keep identifiers and long filenames from forcing panes
wider than their available space.

`StatusChip` provides `default`, `good`, `warn`, `danger`, and `info` tones. Pair color
with a meaningful label. Theme support alone does not establish contrast compliance;
check the rendered text, disabled states, and status colors in each theme.

## Panes and navigation

The document does not scroll; pane bodies own their scroll areas. Preserve `min-h-0`
and `min-w-0` through flex layouts and give the actual scrolling element its overflow
behavior. Headers, composers, and dialog actions should remain reachable while their
content scrolls.

Views are registered in `workspace-tabs`; the shell and pane picker consume that
registry. Use its labels and visibility flags rather than maintaining another nav
list. [AppShell](src/components/AppShell.tsx) owns shell navigation. Routes and view
state must retain the intended instance, conversation, and workspace context.

Chat, terminal, files, and desktop are tiling panes, not a fixed three-column layout.
Use the workspace's existing split, close, and focus actions. Focus behavior belongs
to [Workspace](src/components/workspace/Workspace.tsx) and the
[focus-mode store](src/store/focus-mode-store.ts); a feature should not introduce its
own fullscreen layer or unrelated exit-focus control.

Conversation views can remain mounted while hidden. Use the existing visibility
context when scheduling view work and preserve scroll position, selections, and
in-flight state. Do not use remounting as a routine refresh mechanism.

## Actions, menus, and dialogs

Use `HeaderActionButton`, `HeaderPrimaryButton`, and `HeaderActionDropdown` for compact
header controls. Icon-only controls need an accessible name; labels and active states
must still make sense when the header becomes narrow. Header CSS is intentionally
scoped so its raised button styling does not leak into menu rows.

Register pane actions through the existing pane action mechanism. `PaneActions`
measures available width and moves overflow into a portaled panel. Keep the same
controls and action order in both locations. Do not build a separate desktop-only
overflow menu. Use the anchor helpers for menus that must track their trigger while
nested panes scroll or resize, and keep overlays within the viewport.

For confirmations and forms, follow the appropriate existing dialog component.
`RemoteRuntimeDialog` uses the native `<dialog>` element and a portal; other named
dialogs manage their own focus and dismissal. There is no universal `Modal` component
to import. Prefer these in-app patterns over blocking `window.alert` or
`window.confirm` calls.

Provide a clear title, named close control, and an explicit action. Match the existing
Escape/backdrop dismissal behavior; where an operation prevents dismissal, reflect
that state consistently in the controls. Keep long content scrollable without hiding
the footer. Explain the affected items and consequences before destructive actions.
Detailed paths, verification output, and identifiers belong below the primary task,
not above the decision the user needs to make.

## Forms, feedback, and file content

Follow `WorkspaceEditor` for labeled inputs, grouped choices, keyboard-accessible
steps, validation, and loading states. Creation steps and editing tabs have different
navigation rules. Preserve native input semantics and submit behavior instead of
making a clickable container impersonate a form control. Reuse the existing logo
assets for distribution and tool choices.

Represent loading, empty, unavailable, failed, and completed states explicitly. Show
the action that can resolve an error near the affected content, and keep independent
panes usable. Machine connectivity, installed runtime version, and workspace readiness
are different facts; one green badge must not imply all three succeeded.

File content belongs in the Files pane's preview area. Reuse its common loading/error
layout and download action. Text, sandboxed HTML, and media have different renderers;
unsupported types or codecs need an honest fallback. Native PDF/image/audio/video
previews should retain their accessible titles or alternative text and playback
controls. Upload progress belongs to the workspace operation and must remain meaningful
when the user switches conversations.

## Review a UI change

Check the result in light and dark themes, a narrow split pane, and focus mode. Verify
long content, loading/error states, keyboard operation, visible focus, and overlay
dismissal. Respect `prefers-reduced-motion` when adding motion. Preserve accessible
names, labels, and state attributes rather than relying on color or icons alone.

Use the relevant browser tests under [tests](tests/) for behavior that crosses layout,
focus, or retained-state boundaries. Prefer a small test of the real interaction over
reconstructing component internals or asserting incidental markup. A screenshot is
useful for visual review, but does not establish keyboard or accessibility correctness.
