import type { WorkspaceDesktop } from '../../types/api';
import { workspaceDesktops } from './workspaceDesktops';
import './desktop-preview.css';

export function DesktopPreview({ desktop }: { desktop: WorkspaceDesktop }) {
  const { name: label, detail, capture } = workspaceDesktops[desktop];
  const name = desktop === 'none' ? 'No desktop' : label;
  return <figure className="desktop-preview" aria-label={`${name} preview`}>
    <div className="desktop-preview-heading"><strong>{name}</strong></div>
    {capture && <img className="desktop-preview-screen" src={capture.src} width={1280} height={800} alt={`${name} running in a Sentinel test workspace on ${capture.distribution}`} />}
    <figcaption>{detail}{capture ? ` Shown on ${capture.distribution}; themes and settings may differ.` : ''}</figcaption>
  </figure>;
}
