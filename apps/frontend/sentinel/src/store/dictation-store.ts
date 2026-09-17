import { create } from 'zustand';

/** Window-local microphone ownership; never persisted or sent to an agent. */
export const useDictationStore = create<{ owner: string | null }>(() => ({ owner: null }));
