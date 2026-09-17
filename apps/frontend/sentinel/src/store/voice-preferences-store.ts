import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import { useMemo } from 'react';
import type { SessionModelChoice } from '../lib/model-selection';
import type { ModelOption } from '../types/api';

export type VoiceModelSelection = SessionModelChoice & { tier: ModelOption['tier'] };

export const VOICE_SPEED = { min: .75, max: 2, step: .05, default: 1.05 };
function normalizeSpeed(speed: number): number {
  return Number.isFinite(speed)
    ? Math.round(Math.min(VOICE_SPEED.max, Math.max(VOICE_SPEED.min, speed)) * 20) / 20
    : VOICE_SPEED.default;
}

export const useVoicePreferences = create<{
  providers: Record<string, string>;
  selections: Record<string, VoiceModelSelection>;
  speeds: Record<string, number>;
  /** Play a tick when Voice hears you and a tone when it starts thinking. */
  cues: boolean;
  /** Input deviceId for this machine; '' follows the system default. */
  microphone: string;
  setProvider: (instance: string, provider: string) => void;
  setSelection: (instance: string, selection: VoiceModelSelection) => void;
  setSpeed: (instance: string, speed: number) => void;
  setCues: (enabled: boolean) => void;
  setMicrophone: (deviceId: string) => void;
}>()(persist(set => ({
  providers: {}, selections: {}, speeds: {}, microphone: '', cues: true,
  setProvider: (instance, provider) => set(state => ({ providers: { ...state.providers, [instance]: provider }, selections: { ...state.selections, [instance]: {tier:'fast', provider_id:provider || undefined} } })),
  setSelection: (instance, selection) => set(state => ({ selections: { ...state.selections, [instance]: selection }, providers: { ...state.providers, [instance]: selection.provider_id ?? '' } })),
  setSpeed: (instance, speed) => set(state => ({ speeds: { ...state.speeds, [instance]: normalizeSpeed(speed) } })),
  setCues: enabled => set({ cues: enabled }),
  setMicrophone: deviceId => set({ microphone: deviceId }),
}), { name: 'sentinel.voice-preferences', storage: createJSONStorage(() => localStorage), partialize: state => ({ providers: state.providers, selections: state.selections, speeds: state.speeds, microphone: state.microphone, cues: state.cues }) }));

export function useMicrophone(): string {
  return useVoicePreferences(state => state.microphone);
}

export function useVoiceSpeed(instance: string): number {
  return useVoicePreferences(state => normalizeSpeed(state.speeds[instance] ?? VOICE_SPEED.default));
}

export function useVoiceModelSelection(instance: string): VoiceModelSelection {
  const selection = useVoicePreferences(state => state.selections[instance]);
  const legacyProvider = useVoicePreferences(state => state.providers[instance]);
  return useMemo(() => selection ?? {tier:'fast', provider_id:legacyProvider || undefined}, [selection, legacyProvider]);
}
