import { useEffect, useLayoutEffect, useState, type ReactNode } from 'react';
import { usePaneId } from '../../lib/workspace-context';
import { clearPaneActions, setPaneActions } from '../../store/pane-actions-store';

export function FilesPaneControls({ children }: { children: ReactNode }) {
  const paneId = usePaneId();
  const [headerVisible, setHeaderVisible] = useState(false);
  useEffect(() => { if (paneId) setPaneActions(paneId, children); }, [paneId, children]);
  useEffect(() => { if (paneId) return () => clearPaneActions(paneId); }, [paneId]);
  useLayoutEffect(() => {
    if (!paneId) return;
    // Maximized/focused layouts can hide Dockview's tab strip. Keep essential
    // actions visible there using the same bar, rather than silently losing them.
    const check = () => {
      const header = document.querySelector<HTMLElement>(`[data-pane-header-id="${CSS.escape(paneId)}"]`);
      setHeaderVisible(!!header && header.getBoundingClientRect().height > 0 && header.getBoundingClientRect().width > 0 && getComputedStyle(header).visibility !== 'hidden');
    };
    check();
    const observer = new MutationObserver(check);
    observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'style'] });
    window.addEventListener('resize', check);
    return () => { observer.disconnect(); window.removeEventListener('resize', check); };
  }, [paneId]);
  return paneId && headerVisible ? null : <header className="project-topbar chat-header-actions pr-visible-pane-bar">{children}</header>;
}
