import { useCallback, useRef, useState, type SetStateAction } from 'react';

/** Session storage survives renderer refreshes; keys are scoped to the pane. */
export function usePaneState<T>(key: string, initial: T | (() => T)) {
  const [value, update] = useState<T>(() => {
    try { const saved = sessionStorage.getItem(key); if (saved !== null) return JSON.parse(saved); } catch { /* Storage is optional. */ }
    return typeof initial === 'function' ? (initial as () => T)() : initial;
  });
  const current = useRef(value);
  const setValue = useCallback((next: SetStateAction<T>) => {
    const previous = current.current;
    const result = typeof next === 'function' ? (next as (value: T) => T)(previous) : next;
    try { sessionStorage.setItem(key, JSON.stringify(result)); } catch { /* Keep the live state if storage is full. */ }
    current.current = result;
    update(result);
  }, [key]);
  return [value, setValue] as const;
}
