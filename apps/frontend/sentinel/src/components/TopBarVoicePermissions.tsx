import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { ShieldCheck } from 'lucide-react';
import { createPortal } from 'react-dom';
import { useSessionGrants } from './session/SessionPermissions';

/** Session permissions for the Voice conversation, revocable from the island. */
export function TopBarVoicePermissions({ instanceName, sessionId }: { instanceName: string; sessionId: string }) {
  const { grants, loading, error, busy, revoke, retry } = useSessionGrants(instanceName, sessionId);
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const id = useId();

  const close = useCallback(() => setOpen(false), []);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !button.current?.contains(event.target) && !panel.current?.contains(event.target)) close();
    };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') close(); };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); };
  }, [open, close]);

  // Voice's overlay sits in the top layer; a plain portal would render dimmed beneath it.
  useEffect(() => {
    const node = panel.current;
    if (!node) return;
    if (open && !node.matches(':popover-open')) node.showPopover();
    else if (!open && node.matches(':popover-open')) node.hidePopover();
  }, [open]);

  const anchor = button.current?.getBoundingClientRect();
  return <>
    <button ref={button} type="button" className="topbar-voice-settings topbar-voice-permissions" data-count={grants.length || undefined}
      aria-label={`Session permissions · ${grants.length}`} title={`Session permissions · ${grants.length}`} aria-expanded={open} aria-controls={id}
      onClick={() => setOpen(value => !value)}>
      <ShieldCheck size={16} />
      {grants.length > 0 && <span className="topbar-voice-permissions-count" aria-hidden="true">{grants.length}</span>}
    </button>
    {createPortal(<div ref={panel} id={id} popover="manual" role="dialog" aria-label="Session permissions" className="topbar-voice-permissions-panel session-permissions"
      style={anchor ? { top: anchor.bottom + 8, left: anchor.left + anchor.width / 2 } : undefined}>
      <div className="session-permissions-body">
        {loading ? <p role="status">Loading…</p> : <>
          <p>{grants.length ? 'These actions run without asking in this Voice conversation.' : 'No actions automatically approved in this Voice conversation.'}</p>
          <ul>{grants.map(grant => <li key={grant.id}>
            <code>{grant.action}</code>
            <button type="button" disabled={busy !== null} aria-label={`Revoke ${grant.action}`} onClick={() => void revoke(grant)}>{busy === grant.id ? 'Revoking…' : 'Revoke'}</button>
          </li>)}</ul>
        </>}
        {error && <p role="alert">{error} <button type="button" onClick={retry}>Retry</button></p>}
      </div>
    </div>, document.body)}
  </>;
}
