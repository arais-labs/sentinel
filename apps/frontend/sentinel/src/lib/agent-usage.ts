import type { AgentUsage } from '../types/api';

export function formatAgentCost(usage?: AgentUsage | null): string {
  if (!usage) return '—';
  const costs = Object.entries(usage.costs);
  const priced = costs.some(([, value]) => value.priced_requests > 0);
  if (!priced) return usage.requests === 0 ? '$0.00' : '—';
  const amount = costs.reduce((sum, [, value]) => sum + Number(value.usd), 0);
  const partial = usage.unreported_requests > 0 || usage.history_incomplete || costs.some(([, value]) => value.unpriced_requests > 0);
  return `${costs.some(([kind]) => kind === 'api_equivalent') ? '≈ ' : ''}${amount.toLocaleString('en-US', {
    style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: amount > 0 && amount < .01 ? 6 : 2,
  })}${partial ? '*' : ''}`;
}
