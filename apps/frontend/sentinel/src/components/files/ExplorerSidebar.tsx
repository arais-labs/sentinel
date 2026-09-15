import type { ReactNode } from 'react';
import './explorer-sidebar.css';

/** Keep content mounted so opening and closing use the same motion everywhere. */
export function ExplorerSidebar({ open, width, collapsedWidth = 0, overlay = false, className = '', children }: {
  open: boolean; width: number; collapsedWidth?: number; overlay?: boolean; className?: string; children: ReactNode;
}) {
  return <div className={`explorer-sidebar ${className}`} data-open={open} data-overlay={overlay} inert={!open}
    style={{ width: open ? width : collapsedWidth }}>
    <div className="explorer-sidebar-inner" style={{ width }}>{children}</div>
  </div>;
}
