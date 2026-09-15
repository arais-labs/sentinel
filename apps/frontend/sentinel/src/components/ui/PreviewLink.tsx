import type { AnchorHTMLAttributes, MouseEvent } from 'react';
import { notificationPublisher } from '../../lib/notifications';
import { previewTargetPath } from '../../../../../desktop/sentinel/src/shared/preview';

const notify = notificationPublisher('Preview');

export function PreviewLink({ href, children, onClick, onAuxClick, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) {
  const open = (event: MouseEvent<HTMLAnchorElement>) => {
    if (event.button > 1) { onAuxClick?.(event); return; }
    if (!href || !previewTargetPath(href)) { onClick?.(event); return; }
    event.preventDefault();
    if (!window.sentinelDesktop?.openPreview) {
      notify.error('Open this preview from the Sentinel desktop app.');
      return;
    }
    void window.sentinelDesktop.openPreview(href).catch(error => notify.error(error instanceof Error ? error.message : 'Could not open preview'));
  };
  return <a {...props} href={href} target="_blank" rel="noopener noreferrer" onClick={open} onAuxClick={open}>{children}</a>;
}
