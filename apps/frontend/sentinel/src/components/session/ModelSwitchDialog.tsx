import { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { AlertCircle, ArrowRight, X } from 'lucide-react';
import { ProviderLogo, providerLabel } from './SessionModelControls';

export function ModelSwitchDialog({ model, providerId, reasoning, exceedsContext, onCancel, onConfirm }: {
  model: string; providerId?: string; reasoning?: string; exceedsContext: boolean;
  onCancel: () => void; onConfirm: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    const previous = document.activeElement;
    dialog?.showModal();
    return () => { dialog?.close(); if (previous instanceof HTMLElement && previous.isConnected) previous.focus(); };
  }, []);
  return createPortal(<dialog ref={ref} className="model-switch-dialog" aria-labelledby="model-switch-title" aria-describedby="model-switch-description"
    onCancel={event => { event.preventDefault(); onCancel(); }} onClick={event => { if (event.target === event.currentTarget) onCancel(); }}>
    <div className="model-switch-body">
      <div className="model-switch-heading"><span className="model-switch-symbol"><AlertCircle size={18} /></span><h2 id="model-switch-title">Switch on a best-effort basis?</h2><button aria-label="Close" onClick={onCancel}><X size={16} /></button></div>
      <div className="model-switch-target"><ProviderLogo id={providerId} /><div><strong>{providerLabel(providerId)}</strong><span>{model}</span></div>{reasoning && <span className="model-switch-effort">{reasoning} reasoning</span>}</div>
      <p id="model-switch-description">{exceedsContext ? 'Your current context exceeds this model’s limit.' : 'The context size could not be verified for this selection.'} The next request may not fit.</p>
      <p className="model-switch-note">Your conversation history stays intact.</p>
      <div className="model-switch-actions"><button autoFocus onClick={onCancel}>Cancel</button><button className="model-switch-confirm" onClick={onConfirm}>Switch anyway<ArrowRight size={14} /></button></div>
    </div>
  </dialog>, document.body);
}
