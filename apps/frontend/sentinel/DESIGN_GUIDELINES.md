# Sentinel Frontend Design Guidelines

## Direction
The Sentinel UI is an operator control plane: dense, calm, and precise. It is built to
drive **multiple logical instances from one deployment** — the same React app runs
in the Docker Compose stack and inside the bundled macOS desktop app — so every screen
is instance-aware and optimized for sustained, high-signal monitoring rather than
marketing polish.

## Principles
1. Prioritize signal over decoration.
2. Keep interaction surfaces flat and predictable.
3. Use space for hierarchy, not for visual effects.
4. Make live state obvious (streaming, running, failed, disconnected).
5. Every pane should have explicit scroll boundaries.
6. Keep the active instance unambiguous — the user always knows which instance a view targets.

## Visual System

### Theming and tokens
Colors are driven by **CSS custom properties**, not hard-coded hex values, so the app
supports both a **dark** and a **light** theme. The theme defaults to **dark**, is
toggled via the theme store (`src/store/theme-store.ts`), persisted to
`localStorage`, and applied by toggling the `dark` class on `<html>` (Tailwind runs in
`darkMode: 'class'`). Always style against the tokens below — never inline a raw color
that only works in one theme.

Tokens are defined in `src/index.css` (`:root` for light, `html.dark` for dark):

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| `--app-bg` / `--surface-0` | `#ffffff` | `#09090b` | App background / primary surface |
| `--surface-1` | `#f8fafc` | `#111113` | Raised surface |
| `--surface-2` | `#f1f5f9` | `#18181b` | Inset / hover surface |
| `--surface-3` | `#e2e8f0` | `#27272a` | Strong fill |
| `--border-subtle` | `#e2e8f0` | `#1f1f22` | Default 1px borders |
| `--border-strong` | `#cbd5e1` | `#2e2e32` | Emphasis borders |
| `--text-primary` | `#0f172a` | `#f4f4f5` | Body / headings |
| `--text-secondary` | `#475569` | `#a1a1aa` | Secondary text |
| `--text-muted` | `#94a3b8` | `#52525b` | Metadata / disabled |
| `--accent-solid` | `#0f172a` | `#ffffff` | Filled primary action |

In Tailwind, reference tokens with the arbitrary-value syntax, e.g.
`bg-[color:var(--surface-0)]`, `border-[color:var(--border-subtle)]`,
`text-[color:var(--text-secondary)]`.

### Status colors
Status uses semantic Tailwind scales (with dark-mode variants) rather than tokens, so a
failing run reads the same in both themes:

- Success / good: emerald
- Warning: amber / orange
- Danger: rose
- Info: sky

Reuse the `StatusChip` component (see below) instead of re-deriving these per call site.

### Typography
- UI font: **Inter**; monospace (code, IDs, diffs): **JetBrains Mono** (loaded in `src/index.css`).
- Base: 14px
- Tight hierarchy:
  - Page title: 18px semibold
  - Section label: 11-12px uppercase/medium
  - Body: 13-14px
  - Metadata: 11-12px

### Spacing & Radius
- Spacing scale: 4 / 8 / 12 / 16 / 24
- Radius is restrained: chips/badges use `rounded` (4px), most controls `rounded-md`
  (6px), panels/cards use `rounded-lg` (8px). Avoid larger radii.
- Avoid oversized paddings and giant hero gaps.

### Effects
- No gradient backgrounds on surfaces
- No ornamental blobs/glows
- No heavy shadows
- Use subtle borders and hover states only
- Scrollbars are thin (6px) and token-colored — do not restyle per view.

## Layout Rules
1. The app shell fills the viewport (`h-screen`); `body` sets `overflow: hidden` so the
   workspace never page-scrolls. Only inner panes scroll.
2. Any screen with paneled workflows (chat, memory, modules) must set `min-h-0` and an
   explicit `overflow` on each pane so scroll is contained, not inherited.
3. The chat timeline scrolls independently from the session sidebar and the right rail.
4. Preserve high information density on desktop; the left nav and secondary rails
   collapse on smaller screens (`hidden md:flex`).

## App Shell & Navigation
`AppShell` (`src/components/AppShell.tsx`) renders the collapsible left nav. Nav items,
in order: **Sessions, Session Logs, Memory, Triggers, Modules, Approvals, Permissions,
Git, Telegram, Showcase (dev builds only), Settings.**

Navigation is **instance-scoped**: routes are built from the current instance via the
`/instances/:instanceName/<route>` pattern, and instance selection happens on the
manager-level `InstancePickerPage` at `/`. When running inside the desktop app, the
shell may expose desktop-only affordances through `window.sentinelDesktop` (e.g. the
control center); guard all such calls because they are undefined in the browser/compose
build.

## Component Rules
Prefer the shared primitives in `src/components/ui/` over bespoke markup.

### Buttons
- Primary: filled with `--accent-solid`, concise label, no gradients
- Secondary: surface background with a 1px border
- Danger actions: secondary style + rose text

### Inputs
- Flat surface background
- Single border color family (`--border-subtle`, `--border-strong` on focus/hover)
- Focus ring subtle and consistent

### Status Chips
- Use `StatusChip` (`src/components/ui/StatusChip.tsx`).
- Tones: `default | good | warn | danger | info`.
- Small rectangular chips, uppercase, semantic colors only — never a decorative palette.

### Panels
- Use `Panel` (`src/components/ui/Panel.tsx`): `--surface-0` background, 1px
  `--border-subtle`, `rounded-lg`.
- No blur/frosted glass.

### Other primitives
- `Markdown` — markdown with KaTeX + highlight.js, used in chat and page modules.
- `Modal` — portal-based backdrop for confirmations and forms.
- `DynamicForm` / `DynamicDetailPane` — drive module CRUD (data modules) from specs.
- Native browser dialogs (`alert` / `confirm` / `prompt`) are forbidden; use `Modal` and
  in-UI error/toast state instead.

## Product-Specific UX

### Sessions / Chat
- Left: conversations list (per-instance sessions).
- Center: message timeline + composer.
- Right: workbench — tabbed file explorer, git diff viewer, and runtime live view.
- Tool calls render as structured execution cards with payload details.
- Streaming state is always visible in the header (events arrive over the per-session
  runtime WebSocket).
- Approval-gated tool calls surface as pending, not failed — reflect the `pending →
  approved/rejected/timed_out/cancelled` lifecycle in the UI.

### Modules
- Three module shapes: API/tool modules (actions, secrets, code editing), page modules
  (markdown), and data modules (CRUD grid with filter/search + detail pane).
- The module registry is per-instance, so module views always reflect the active
  instance.

### Memory Explorer
- Left: tree/search navigator.
- Right: inspector/details/actions.
- Fast select, clear hierarchy, minimal modal friction.
- Distinguish pinned vs non-pinned nodes; node actions live near the inspected context.

### Admin / Ops Views
- Table/list first.
- Controls grouped by risk (critical actions separated).
- Clear system state badges (status codes, run phases).


## Accessibility
- Keyboard-first navigation in core flows.
- Target minimum contrast AA on text/status (not yet formally validated).
- Focus outlines visible on interactive controls.
- Both themes must remain legible — verify new colors in dark and light.
