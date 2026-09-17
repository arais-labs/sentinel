import type { ModelOption } from '../types/api';

export type ReasoningLevel = 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max' | 'ultra';
export type SessionModelChoice = { provider_id?: string; reasoning_level?: ReasoningLevel; fast_mode?: boolean };

/** An explicit selection never silently becomes a different provider. */
export function resolveModelSelection(model: ModelOption | undefined, choice: SessionModelChoice) {
  const options = model?.provider_options ?? [];
  const provider = choice.provider_id
    ? options.find(option => option.provider_id === choice.provider_id)
    : options[0];
  return {
    provider,
    choice: provider ? {
      provider_id: choice.provider_id,
      reasoning_level: provider.reasoning_levels.includes(choice.reasoning_level ?? '') ? choice.reasoning_level : undefined,
      fast_mode: !!choice.fast_mode && !!provider.supports_fast_mode,
    } : choice,
  };
}
