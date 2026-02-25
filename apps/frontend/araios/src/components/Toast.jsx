import { useEffect } from 'react';

export default function Toast({ toast, onDismiss }) {
  useEffect(() => {
    if (!toast) return;
    const delay = toast.action ? 5000 : 2400;
    const timer = setTimeout(onDismiss, delay);
    return () => clearTimeout(timer);
  }, [toast, onDismiss]);

  if (!toast) return null;

  return (
    <div className={`toast toast-${toast.type}`} role="status">
      <span>{toast.message}</span>
      {toast.action && (
        <button className="toast-undo-btn" onClick={() => { toast.action.onClick(); onDismiss(); }}>
          {toast.action.label}
        </button>
      )}
    </div>
  );
}
