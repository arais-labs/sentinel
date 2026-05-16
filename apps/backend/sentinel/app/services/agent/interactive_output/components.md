Use these classes inside `<!-- sentinel:html -->` responses. The theme CSS is injected for you; no `<style>` block needed.

**Layout**
- `.sentinel-container` — centered max-width wrapper (~960px).
- `.sentinel-stack` — vertical flex stack with consistent gap.
- `.sentinel-grid` — responsive grid; min column width ~220px.
- `.sentinel-card` — bordered surface with padding.
- `.sentinel-section-header` — small caps uppercase label above a section.

**Data**
- `.sentinel-table` — themed `<table>`. Add `.is-sortable` for sort-indicator headers (visual only — wire your own JS for sort behavior if needed).
- `.sentinel-kv` — `<dl>` for key/value pairs (label on left, value on right).
- `.sentinel-stat` — labeled big-number tile; use child `.label` and `.value`.

**Inputs**
- `.sentinel-input`, `.sentinel-select`, `.sentinel-textarea` — themed form controls.
- `.sentinel-checkbox`, `.sentinel-radio` — apply to `<input type="checkbox/radio">`.
- `.sentinel-datetime` — themed `<input type="datetime-local">` (also works for `type="date"` and `type="time"`).

**Buttons**
- `.sentinel-button` — base button. Combine with one variant and optionally a size:
  - Variants: `.is-primary`, `.is-secondary`, `.is-destructive`, `.is-ghost`.
  - Sizes: `.is-sm`, `.is-lg` (default is medium).

**Navigation**
- `.sentinel-tabs` — container around `<details>` elements (each `<details><summary>Tab</summary><div>…</div></details>` is one tab). Set `name="…"` on `<details>` so only one is open at a time. CSS-only; no JS required.

**Feedback**
- `.sentinel-badge` — pill badge. Combine with `.is-info`, `.is-success`, `.is-warning`, `.is-danger`.
- `.sentinel-alert` — boxed message with left border. Same variant suffixes as badge.
- `.sentinel-progress` — wrap a `<div class="bar" style="width:NN%">` to show progress.

**Typography**
- `.sentinel-heading-1` through `.sentinel-heading-4` — sized headings.
- `.sentinel-code` — inline code chip.
- `.sentinel-kbd` — keyboard key chip.
- `.sentinel-link` — themed `<a>` styling.
- `.sentinel-muted` — secondary/muted text.

**Severity coloring**
- `.sentinel-severity-low`, `.sentinel-severity-medium`, `.sentinel-severity-high`, `.sentinel-severity-critical` — color helpers; apply to badges, alerts, or any text element.

Need something not listed? Either add inline CSS after the marker (still themed mode), or switch to `<!-- sentinel:html-raw -->` for full design control.
