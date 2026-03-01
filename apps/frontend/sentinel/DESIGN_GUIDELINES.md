# Sentinel Frontend Design Guidelines

## Direction
Sentinel UI should feel like a professional operator control plane: dense, calm, and precise.

## Principles
1. Prioritize signal over decoration.
2. Keep interaction surfaces flat and predictable.
3. Use space for hierarchy, not for visual effects.
4. Make live state obvious (streaming, running, failed, disconnected).
5. Every pane should have explicit scroll boundaries.

## Visual System

### Color
- Background: neutral slate (`#f7f8fa`)
- Primary surface: white (`#ffffff`)
- Border: `#e2e8f0`
- Primary text: `#0f172a`
- Secondary text: `#64748b`
- Accent/action: slate-dark (`#0f172a`)
- Status:
  - Success: emerald scale
  - Warning: amber scale
  - Danger: rose scale
  - Info: sky scale

### Typography
- Base: 14px
- Tight hierarchy:
  - Page title: 18px semibold
  - Section label: 11-12px uppercase/medium
  - Body: 13-14px
  - Metadata: 11-12px

### Spacing & Radius
- Spacing scale: 4 / 8 / 12 / 16 / 24
- Default radius: 6px (`rounded-md`)
- Panels/cards: 10-12px max
- Avoid oversized paddings and giant hero gaps

### Effects
- No gradient backgrounds
- No ornamental blobs/glows
- No heavy shadows
- Use subtle borders and hover states only

## Layout Rules
1. App shell uses full-height viewport (`100dvh`) and does not page-scroll in workspace mode.
2. Any screen with paneled workflows (chat, memory) must set `min-h-0` and `overflow` on each pane.
3. Chat timeline scrolls independently from sidebar and right rail.
4. Preserve high information density on desktop; collapse secondary rails on smaller screens.

## Component Rules

### Buttons
- Primary: filled dark, concise label, no gradients
- Secondary: white with border
- Danger actions: secondary style + danger text

### Inputs
- Flat white background
- Single border color family
- Focus ring subtle and consistent

### Status Chips
- Small rectangular chips
- Semantic colors only
- Never use bright decorative palette

### Panels
- White surface, 1px border
- Rounded corners only
- No blur/frosted glass

## Product-Specific UX

### Sessions / Chat
- Left: conversations list
- Center: timeline + composer
- Right: runtime tools/sub-agents
- Tool calls displayed as structured execution cards with payload details
- Streaming state always visible in header

### Memory Explorer
- Left: tree/search navigator
- Right: inspector/details/actions
- Fast select, clear hierarchy, minimal modal friction
- Node actions available near inspected context

### Admin / Ops Views
- Table/list first
- Controls grouped by risk (critical actions separated)
- Clear system state badges (ESTOP, status codes)

## Accessibility
- Keyboard-first navigation in core flows
- Minimum contrast AA on text/status
- Focus outlines visible on interactive controls
