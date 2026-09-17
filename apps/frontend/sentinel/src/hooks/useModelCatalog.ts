import { useEffect, useRef, useState } from 'react';
import { invalidateModelCatalog, loadModelCatalog } from '../lib/model-catalog';
import type { ModelsResponse } from '../types/api';

const changedEvent = 'sentinel:providers-changed';
const empty: ModelsResponse = { models: [], default_tier: null };

export function providersChanged(instance: string | null | undefined) {
  if (!instance) return;
  invalidateModelCatalog(instance);
  window.dispatchEvent(new CustomEvent(changedEvent, { detail: { instance } }));
}

/** Refresh the runtime catalog, not a second list inferred from credential status. */
export function useModelCatalog(instance: string | null, visible = true, pickerOpen = false) {
  const [state, setState] = useState<{ instance: string; catalog: ModelsResponse } | null>(null);
  const refreshRef = useRef<() => void>(() => {});
  useEffect(() => {
    if (!instance) return;
    let disposed = false;
    let generation = 0;
    const refresh = () => {
      const current = ++generation;
      void loadModelCatalog(instance)
        .then(catalog => {
          if (!disposed && current === generation) setState({ instance, catalog });
        })
        .catch(() => { /* Keep the last successful catalog on transient failure. */ });
    };
    refreshRef.current = refresh;
    const focus = () => { if (visible && !document.hidden) refresh(); };
    const changed = (event: Event) => {
      if ((event as CustomEvent<{ instance: string }>).detail.instance === instance) refresh();
    };
    if (visible) refresh();
    window.addEventListener('focus', focus);
    window.addEventListener(changedEvent, changed);
    return () => {
      disposed = true;
      refreshRef.current = () => {};
      window.removeEventListener('focus', focus);
      window.removeEventListener(changedEvent, changed);
    };
  }, [instance, visible]);
  useEffect(() => {
    if (visible && pickerOpen) refreshRef.current();
  }, [visible, pickerOpen]);
  return state?.instance === instance ? state.catalog : empty;
}
