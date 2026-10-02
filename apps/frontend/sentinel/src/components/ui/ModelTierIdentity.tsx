import { Brain, Sparkles, Zap } from 'lucide-react';
import type { ReactNode } from 'react';
import type { ModelOption } from '../../types/api';
import './model-tier-identity.css';

export const modelTiers = [
  { tier: 'fast', label: 'Fast', description: 'Quick responses, minimal reasoning', Icon: Zap },
  { tier: 'normal', label: 'Normal', description: 'Balanced quality and speed', Icon: Sparkles },
  { tier: 'hard', label: 'Deep Think', description: 'Extended reasoning for complex problems', Icon: Brain },
] as const;

/** Shared tier identity for chat, Voice, and provider configuration. */
export function ModelTierIdentity({ tier, label, description, compact = false, active = false, className = '', children }: {
  tier: ModelOption['tier'];
  label?: string;
  description?: string;
  compact?: boolean;
  active?: boolean;
  className?: string;
  children?: ReactNode;
}) {
  const identity = modelTiers.find(item => item.tier === tier)!;
  const Icon = identity.Icon;
  return <span className={`model-tier-identity ${compact ? 'is-compact' : ''} ${active ? 'is-active' : ''} ${className}`} data-tier={tier}>
    <Icon size={14} className="model-tier-icon" aria-hidden="true" />
    <span className="model-tier-copy">
      <span className="model-tier-name">{label ?? identity.label}</span>
      {!compact && <span className="model-tier-description">{description ?? identity.description}</span>}
      {children}
    </span>
  </span>;
}
