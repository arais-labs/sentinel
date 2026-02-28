export default function ConfirmDialog({ title, message, confirmLabel = 'Delete', onConfirm, onCancel }) {
  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal animate-in" style={{ maxWidth: '400px' }} onClick={(e) => e.stopPropagation()} role="alertdialog" aria-modal="true" aria-label={title}>
        <div className="modal-header">
          <h2 className="modal-title">{title}</h2>
          <button className="p-2 text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] hover:bg-[color:var(--surface-2)] rounded-md transition-all" onClick={onCancel} aria-label="Close">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18"></line>
              <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
          </button>
        </div>
        <div className="p-6">
          <p className="text-sm leading-relaxed text-[color:var(--text-secondary)] font-medium">
            {message}
          </p>
        </div>
        <div className="modal-footer">
          <button className="btn-secondary" style={{ flex: 1 }} onClick={onCancel}>Cancel</button>
          <button className="btn-danger" style={{ flex: 1 }} onClick={onConfirm}>{confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}
