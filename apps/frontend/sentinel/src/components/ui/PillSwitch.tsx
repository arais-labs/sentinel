import type { ReactNode } from 'react';
import './pill-switch.css';

interface PillOption<T extends string> {
  value: T;
  label: string;
  icon?: ReactNode;
}

/** Shared two-way selector with the Workspace / Machines sliding pill treatment. */
export function PillSwitch<T extends string>({ label, value, options, onChange, className = '' }: {
  label: string;
  value: T;
  options: readonly [PillOption<T>, PillOption<T>];
  onChange: (value: T) => void;
  className?: string;
}) {
  return <div className={`pill-switch ${className}`} role="group" aria-label={label}>
    <span aria-hidden="true" className="pill-switch-indicator"
      style={{ transform: `translateX(${value === options[1].value ? 100 : 0}%)` }} />
    {options.map(option => <button key={option.value} type="button" aria-pressed={value === option.value}
      onClick={() => onChange(option.value)}>{option.icon}{option.label}</button>)}
  </div>;
}
