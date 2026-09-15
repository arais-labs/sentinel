import { clsx } from 'clsx';
import { forwardRef } from 'react';
import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';

// Compact header-action controls. These render inside the ~40px tiling pane
// header (between the view-switcher and split/close) as well as standalone in
// the full 64px page header (non-workspace mode), so the chrome is intentionally
// subtle and small. Sentence-case labels, h-7 height, text-xs.

const COMPACT_BASE =
  'inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium ' +
  'shadow-xs transition-all active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50';

// Idle utility surface: a faint top-down surface gradient + a slightly stronger
// idle border so the control reads as a real, tappable chip rather than a flat
// grey rectangle. Hover bumps the surface/border.
const UTILITY_IDLE =
  'text-(--text-secondary) border border-(--border-strong) ' +
  'bg-linear-to-b from-(--surface-1) to-(--surface-2) ' +
  'hover:from-(--surface-2) hover:to-(--surface-2) ' +
  'hover:text-(--text-primary) hover:border-(--border-strong)';

// Toggled-on / selected utility state: subtle accent surface so it reads as
// "active" without the heavy fill of the primary button.
const UTILITY_ACTIVE =
  'text-(--accent-solid) bg-(--surface-2) border border-(--accent-solid)/40 ' +
  'hover:bg-(--surface-2) hover:border-(--accent-solid)/60';

// Pill family for dropdown triggers: the original rounded-full Sessions pill the
// user liked — soft surface, colored leading icon (via `iconClassName`), shadow.
const PILL_BASE =
  'inline-flex h-7 items-center gap-2 rounded-full px-3 text-[11px] font-semibold ' +
  'shadow-xs transition-all active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50';

const PILL_IDLE =
  'text-(--text-primary) border border-(--border-subtle) bg-(--surface-1) ' +
  'hover:bg-(--surface-2) hover:border-(--border-strong)';

const PILL_ACTIVE =
  'text-(--text-primary) border border-(--border-strong) bg-(--surface-2)';

type ButtonBaseProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'>;

export interface HeaderActionButtonProps extends ButtonBaseProps {
  /** Leading icon (already sized by the caller, e.g. lucide icon at size ~14). */
  icon?: ReactNode;
  /** Sentence-case label. Omit for an icon-only square button. */
  label?: ReactNode;
  /** Renders the toggled-on / selected accent state. */
  active?: boolean;
  /** Optional tint for the leading icon wrapper (e.g. `text-amber-500/80`). */
  iconClassName?: string;
}

/**
 * Utility / secondary compact header button. Supports icon + label, an icon-only
 * mode (omit `label`), and an `active` toggle state. Forwards its ref so it can
 * double as a `useAnchorRect` anchor for a portaled dropdown menu.
 */
export const HeaderActionButton = forwardRef<HTMLButtonElement, HeaderActionButtonProps>(
  function HeaderActionButton({ icon, label, active = false, iconClassName, className, type, ...rest }, ref) {
    const iconOnly = label == null;
    return (
      <button
        ref={ref}
        type={type ?? 'button'}
        className={clsx(
          COMPACT_BASE,
          active ? UTILITY_ACTIVE : UTILITY_IDLE,
          iconOnly && 'w-7 justify-center px-0',
          className,
        )}
        {...rest}
      >
        {icon != null && (
          <span className={clsx('inline-flex shrink-0', !active && iconClassName)}>{icon}</span>
        )}
        {label}
      </button>
    );
  },
);

export interface HeaderPrimaryButtonProps extends ButtonBaseProps {
  icon?: ReactNode;
  label?: ReactNode;
}

/**
 * The page's main create/affirmative action. Same compact size as the utility
 * button but filled with the accent color, with a faint top sheen + shadow so it
 * has a touch more depth than a flat fill.
 */
export const HeaderPrimaryButton = forwardRef<HTMLButtonElement, HeaderPrimaryButtonProps>(
  function HeaderPrimaryButton({ icon, label, className, type, ...rest }, ref) {
    return (
      <button
        ref={ref}
        type={type ?? 'button'}
        className={clsx(
          COMPACT_BASE,
          'bg-(--accent-solid) text-(--app-bg) border border-(--accent-solid) ' +
            'shadow-[0_1px_2px_rgba(0,0,0,0.18),inset_0_1px_0_rgba(255,255,255,0.18)] hover:opacity-90',
          label == null && 'w-7 justify-center px-0',
          className,
        )}
        {...rest}
      >
        {icon}
        {label}
      </button>
    );
  },
);

/** Thin vertical divider between compact action groups. */
export function HeaderActionDivider({ className }: { className?: string }) {
  return <div className={clsx('h-4 w-px bg-(--border-subtle)', className)} aria-hidden="true" />;
}

export interface HeaderActionDropdownProps extends ButtonBaseProps {
  icon?: ReactNode;
  /** Current value shown in the pill. */
  label?: ReactNode;
  /** Drives the chevron rotation. */
  open?: boolean;
  active?: boolean;
  /** Tint for the leading icon (e.g. `text-emerald-500/80`) — the colored dot the user liked. */
  iconClassName?: string;
}

/**
 * Compact dropdown trigger — the original rounded-full Sessions pill: soft
 * surface, a colored leading icon (`iconClassName`), value label, and a rotating
 * chevron. Restyles the existing Effort / Agent-mode / Max triggers; the portaled
 * menu they open is owned by the caller. Forwards its ref so the caller can pass
 * it (or a wrapping ref) to `useAnchorRect`.
 */
export const HeaderActionDropdown = forwardRef<HTMLButtonElement, HeaderActionDropdownProps>(
  function HeaderActionDropdown(
    { icon, label, open = false, active = false, iconClassName, className, type, ...rest },
    ref,
  ) {
    return (
      <button
        ref={ref}
        type={type ?? 'button'}
        aria-haspopup="menu"
        aria-expanded={open}
        className={clsx(
          PILL_BASE,
          open || active ? PILL_ACTIVE : PILL_IDLE,
          className,
        )}
        {...rest}
      >
        {icon != null && (
          <span className={clsx('inline-flex shrink-0', iconClassName ?? 'text-(--text-secondary)')}>
            {icon}
          </span>
        )}
        {label != null && <span className="truncate">{label}</span>}
        <ChevronDown
          size={13}
          className={clsx('opacity-50 transition-transform duration-200', open && 'rotate-180')}
        />
      </button>
    );
  },
);
